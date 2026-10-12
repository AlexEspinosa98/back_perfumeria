-- Stock por cantidad, costo por perfume, ventas, gastos y reportes contables.
-- Ejecutar en Supabase → SQL Editor después de 0001 y 0002.

-- ───────────────────────── Stock y costo en perfumes ─────────────────────────
alter table public.perfumes
  add column stock int not null default 0 check (stock >= 0),
  add column cost  numeric(12,0) not null default 0 check (cost >= 0);   -- precio base (lo que te cuesta)

-- "Disponible" se deriva del stock: con 0 unidades queda agotado automáticamente.
create or replace function public.sync_in_stock() returns trigger language plpgsql as $$
begin new.in_stock := new.stock > 0; return new; end $$;

create trigger perfumes_sync_in_stock before insert or update of stock on public.perfumes
  for each row execute function public.sync_in_stock();

-- Perfumes que ya estaban marcados como disponibles arrancan con 10 unidades (ajústalo en el panel).
update public.perfumes set stock = 10 where in_stock and stock = 0;

-- El costo es privado: el público (anon/authenticated) solo puede leer las columnas comerciales.
revoke select on public.perfumes from anon, authenticated;
grant select (id, name, brand, description, presentation, price, image_url, badge,
              is_active, is_featured, in_stock, created_at, updated_at)
  on public.perfumes to anon, authenticated;

-- ───────────────────────── Pedidos: cómo quiere ser atendido + stock reservado ─────────────────────────
alter table public.orders
  add column contact_via    text not null default 'whatsapp' check (contact_via in ('reserve', 'whatsapp')),
  add column stock_reserved boolean not null default true;

-- ───────────────────────── Contabilidad ─────────────────────────
create table public.sales (
  id               uuid primary key default gen_random_uuid(),
  sold_at          timestamptz not null default now(),
  channel          text not null default 'whatsapp' check (channel in ('web', 'whatsapp', 'presencial', 'otro')),
  customer_name    text not null default '',
  note             text not null default '',
  order_id         uuid unique references public.orders(id) on delete set null,   -- si viene de un pedido web
  stock_decremented boolean not null default false,                                 -- venta manual que descontó stock
  created_by       uuid references public.profiles(id),
  created_at       timestamptz not null default now()
);

create table public.sale_items (
  id          uuid primary key default gen_random_uuid(),
  sale_id     uuid not null references public.sales(id) on delete cascade,
  perfume_id  uuid references public.perfumes(id) on delete set null,
  name        text not null,                                            -- snapshot
  quantity    int  not null check (quantity > 0),
  unit_price  numeric(12,0) not null check (unit_price >= 0),            -- en cuánto se vendió
  unit_cost   numeric(12,0) not null default 0 check (unit_cost >= 0)    -- costo al momento de la venta
);

create table public.expenses (
  id          uuid primary key default gen_random_uuid(),
  spent_at    date not null default current_date,
  amount      numeric(12,0) not null check (amount > 0),
  reason      text not null check (char_length(reason) between 2 and 200),
  category    text not null default 'otros',
  note        text not null default '',
  created_by  uuid references public.profiles(id),
  created_at  timestamptz not null default now()
);

create index sales_sold_at_idx on public.sales (sold_at desc);
create index sale_items_sale_idx on public.sale_items (sale_id);
create index expenses_spent_idx on public.expenses (spent_at desc);

alter table public.sales      enable row level security;
alter table public.sale_items enable row level security;
alter table public.expenses   enable row level security;
create policy sales_admin      on public.sales      for all using (public.is_admin()) with check (public.is_admin());
create policy sale_items_admin on public.sale_items for all using (public.is_admin()) with check (public.is_admin());
create policy expenses_admin   on public.expenses   for all using (public.is_admin()) with check (public.is_admin());

-- ───────────────────────── Funciones (las llama el backend con service role) ─────────────────────────

-- Crea el pedido de forma atómica: valida y descuenta stock, toma precios del servidor.
create or replace function public.place_order(
  p_user uuid, p_items jsonb, p_contact_via text,
  p_name text, p_phone text, p_address text, p_city text, p_notes text
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  v_order public.orders;
  it jsonb;
  p public.perfumes;
  v_qty int;
  v_total numeric := 0;
begin
  insert into public.orders (user_id, contact_via, shipping_name, shipping_phone, shipping_address, shipping_city, notes)
  values (p_user, p_contact_via, p_name, p_phone, p_address, p_city, p_notes)
  returning * into v_order;

  for it in select * from jsonb_array_elements(p_items) loop
    v_qty := (it->>'quantity')::int;
    update public.perfumes set stock = stock - v_qty
     where id = (it->>'perfume_id')::uuid and is_active and stock >= v_qty
    returning * into p;
    if not found then
      raise exception 'insufficient_stock:%',
        coalesce((select name from public.perfumes where id = (it->>'perfume_id')::uuid), it->>'perfume_id');
    end if;
    insert into public.order_items (order_id, perfume_id, name, unit_price, quantity)
    values (v_order.id, p.id, p.name, p.price, v_qty);
    v_total := v_total + p.price * v_qty;
  end loop;

  update public.orders set total = v_total where id = v_order.id returning * into v_order;
  insert into public.order_events (order_id, status, note, created_by)
  values (v_order.id, 'pending', 'Pedido recibido', p_user);
  return to_jsonb(v_order);
end $$;

-- Devuelve (p_reserve=false, al cancelar) o vuelve a apartar (true, al reabrir) el stock de un pedido. Idempotente.
create or replace function public.set_order_stock(p_order uuid, p_reserve boolean)
returns void language plpgsql security definer set search_path = public as $$
declare o public.orders; it record;
begin
  select * into o from public.orders where id = p_order for update;
  if not found then raise exception 'order_not_found'; end if;
  if o.stock_reserved = p_reserve then return; end if;
  for it in select perfume_id, quantity, name from public.order_items
             where order_id = p_order and perfume_id is not null loop
    if p_reserve then
      update public.perfumes set stock = stock - it.quantity where id = it.perfume_id and stock >= it.quantity;
      if not found then raise exception 'insufficient_stock:%', it.name; end if;
    else
      update public.perfumes set stock = stock + it.quantity where id = it.perfume_id;
    end if;
  end loop;
  update public.orders set stock_reserved = p_reserve where id = p_order;
end $$;

-- Convierte un pedido web en venta contable (una sola vez). No toca el stock: ya se descontó al pedir.
create or replace function public.sale_from_order(p_order uuid, p_by uuid)
returns uuid language plpgsql security definer set search_path = public as $$
declare o public.orders; v_sale uuid;
begin
  select id into v_sale from public.sales where order_id = p_order;
  if found then return v_sale; end if;
  select * into o from public.orders where id = p_order;
  if not found then return null; end if;
  insert into public.sales (sold_at, channel, customer_name, note, order_id, created_by)
  values (now(), 'web', o.shipping_name, 'Pedido #' || o.order_number, p_order, p_by)
  returning id into v_sale;
  insert into public.sale_items (sale_id, perfume_id, name, quantity, unit_price, unit_cost)
  select v_sale, oi.perfume_id, oi.name, oi.quantity, oi.unit_price, coalesce(p.cost, 0)
    from public.order_items oi left join public.perfumes p on p.id = oi.perfume_id
   where oi.order_id = p_order;
  return v_sale;
end $$;

-- Registra una venta manual con varios productos (del catálogo o escritos a mano).
-- items: [{perfume_id?, name?, quantity, unit_price, unit_cost?}]
create or replace function public.record_sale(
  p_sold_at timestamptz, p_channel text, p_customer text, p_note text,
  p_items jsonb, p_decrement boolean, p_by uuid
) returns uuid language plpgsql security definer set search_path = public as $$
declare
  v_sale uuid; it jsonb; p public.perfumes;
  v_qty int; v_pid uuid; v_name text; v_cost numeric; v_any_catalog boolean := false;
begin
  if jsonb_array_length(p_items) = 0 then raise exception 'empty_sale'; end if;
  insert into public.sales (sold_at, channel, customer_name, note, created_by)
  values (coalesce(p_sold_at, now()), p_channel, coalesce(p_customer, ''), coalesce(p_note, ''), p_by)
  returning id into v_sale;

  for it in select * from jsonb_array_elements(p_items) loop
    v_qty := (it->>'quantity')::int;
    v_pid := nullif(it->>'perfume_id', '')::uuid;
    if v_pid is not null then
      v_any_catalog := true;
      if p_decrement then
        update public.perfumes set stock = stock - v_qty where id = v_pid and stock >= v_qty returning * into p;
        if not found then
          raise exception 'insufficient_stock:%', coalesce((select name from public.perfumes where id = v_pid), v_pid::text);
        end if;
      else
        select * into p from public.perfumes where id = v_pid;
        if not found then raise exception 'perfume_not_found'; end if;
      end if;
      v_name := p.name;
      v_cost := coalesce(nullif(it->>'unit_cost', '')::numeric, p.cost);
    else
      v_name := nullif(trim(it->>'name'), '');
      if v_name is null then raise exception 'item_name_required'; end if;
      v_cost := coalesce(nullif(it->>'unit_cost', '')::numeric, 0);
    end if;
    insert into public.sale_items (sale_id, perfume_id, name, quantity, unit_price, unit_cost)
    values (v_sale, v_pid, v_name, v_qty, (it->>'unit_price')::numeric, v_cost);
  end loop;

  if p_decrement and v_any_catalog then
    update public.sales set stock_decremented = true where id = v_sale;
  end if;
  return v_sale;
end $$;

-- Elimina una venta; si fue manual y descontó stock, puede devolverlo.
create or replace function public.delete_sale(p_sale uuid, p_restore boolean)
returns void language plpgsql security definer set search_path = public as $$
declare s public.sales;
begin
  select * into s from public.sales where id = p_sale for update;
  if not found then raise exception 'sale_not_found'; end if;
  if p_restore and s.stock_decremented and s.order_id is null then
    update public.perfumes p set stock = p.stock + t.q
      from (select perfume_id, sum(quantity)::int as q from public.sale_items
             where sale_id = p_sale and perfume_id is not null group by perfume_id) t
     where t.perfume_id = p.id;
  end if;
  delete from public.sales where id = p_sale;
end $$;

-- Resumen contable de un periodo (fechas en hora de Colombia, ambos extremos incluidos).
create or replace function public.accounting_summary(p_from date, p_to date)
returns jsonb language sql stable security definer set search_path = public as $$
with s as (
  select sa.id, (sa.sold_at at time zone 'America/Bogota')::date as d, sa.channel,
         si.name, si.quantity, si.unit_price, si.unit_cost
    from public.sales sa join public.sale_items si on si.sale_id = sa.id
   where (sa.sold_at at time zone 'America/Bogota')::date between p_from and p_to
), e as (
  select spent_at, amount, category from public.expenses where spent_at between p_from and p_to
)
select jsonb_build_object(
  'revenue',  coalesce((select sum(quantity * unit_price) from s), 0),
  'cogs',     coalesce((select sum(quantity * unit_cost) from s), 0),
  'expenses', coalesce((select sum(amount) from e), 0),
  'sales_count', (select count(distinct id) from s),
  'units',    coalesce((select sum(quantity) from s), 0),
  'items_without_cost', (select count(*) from s where unit_cost = 0),
  'sales_by_day', coalesce((select jsonb_agg(x order by x.date) from (
      select d as date, sum(quantity * unit_price) as revenue, sum(quantity * unit_cost) as cogs
        from s group by d) x), '[]'::jsonb),
  'expenses_by_day', coalesce((select jsonb_agg(x order by x.date) from (
      select spent_at as date, sum(amount) as amount from e group by spent_at) x), '[]'::jsonb),
  'top_products', coalesce((select jsonb_agg(x) from (
      select name, sum(quantity) as units, sum(quantity * unit_price) as revenue,
             sum(quantity * (unit_price - unit_cost)) as profit
        from s group by name order by sum(quantity * unit_price) desc limit 10) x), '[]'::jsonb),
  'by_channel', coalesce((select jsonb_agg(x) from (
      select channel, count(distinct id) as sales, sum(quantity * unit_price) as revenue
        from s group by channel order by sum(quantity * unit_price) desc) x), '[]'::jsonb),
  'expenses_by_category', coalesce((select jsonb_agg(x) from (
      select category, sum(amount) as amount from e group by category order by sum(amount) desc) x), '[]'::jsonb),
  'inventory', jsonb_build_object(
      'units',          coalesce((select sum(stock) from public.perfumes where is_active), 0),
      'value_at_cost',  coalesce((select sum(stock * cost) from public.perfumes where is_active), 0),
      'value_at_price', coalesce((select sum(stock * price) from public.perfumes where is_active), 0),
      'low_stock',      coalesce((select jsonb_agg(jsonb_build_object('id', id, 'name', name, 'stock', stock) order by stock)
                                    from public.perfumes where is_active and stock <= 3), '[]'::jsonb))
);
$$;

-- Estas funciones solo las usa el backend (service role): nadie más puede invocarlas por la API pública.
revoke all on function public.place_order(uuid, jsonb, text, text, text, text, text, text) from public, anon, authenticated;
revoke all on function public.set_order_stock(uuid, boolean) from public, anon, authenticated;
revoke all on function public.sale_from_order(uuid, uuid) from public, anon, authenticated;
revoke all on function public.record_sale(timestamptz, text, text, text, jsonb, boolean, uuid) from public, anon, authenticated;
revoke all on function public.delete_sale(uuid, boolean) from public, anon, authenticated;
revoke all on function public.accounting_summary(date, date) from public, anon, authenticated;
grant execute on function public.place_order(uuid, jsonb, text, text, text, text, text, text) to service_role;
grant execute on function public.set_order_stock(uuid, boolean) to service_role;
grant execute on function public.sale_from_order(uuid, uuid) to service_role;
grant execute on function public.record_sale(timestamptz, text, text, text, jsonb, boolean, uuid) to service_role;
grant execute on function public.delete_sale(uuid, boolean) to service_role;
grant execute on function public.accounting_summary(date, date) to service_role;

-- Correo opcional para avisar de pedidos nuevos
insert into public.store_settings (key, value) values ('admin_notify_email', '') on conflict (key) do nothing;
