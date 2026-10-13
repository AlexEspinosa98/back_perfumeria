-- Editar ventas ya registradas (con ajuste automático del stock) y reponer stock de forma atómica.
-- Ejecutar en Supabase → SQL Editor después de 0005.

-- Edita cabecera e (opcionalmente) las líneas de una venta.
-- Si la venta fue manual y descontó stock, devuelve lo anterior y descuenta lo nuevo (todo o nada).
-- Si viene de un pedido web, NUNCA toca el stock (lo gobierna el pedido).
-- p_items = null  -> solo cambia fecha/canal/cliente/nota.
create or replace function public.update_sale(
  p_sale uuid, p_sold_at timestamptz, p_channel text, p_customer text, p_note text, p_items jsonb
) returns void language plpgsql security definer set search_path = public as $$
declare
  s public.sales; it jsonb; p public.perfumes;
  v_qty int; v_pid uuid; v_name text; v_cost numeric; v_track boolean;
begin
  select * into s from public.sales where id = p_sale for update;
  if not found then raise exception 'sale_not_found'; end if;

  update public.sales set
    sold_at       = coalesce(p_sold_at, sold_at),
    channel       = coalesce(p_channel, channel),
    customer_name = coalesce(p_customer, customer_name),
    note          = coalesce(p_note, note)
  where id = p_sale;

  if p_items is null then return; end if;
  if jsonb_array_length(p_items) = 0 then raise exception 'empty_sale'; end if;

  v_track := s.stock_decremented and s.order_id is null;
  if v_track then
    update public.perfumes pf set stock = pf.stock + t.q
      from (select perfume_id, sum(quantity)::int as q from public.sale_items
             where sale_id = p_sale and perfume_id is not null group by perfume_id) t
     where t.perfume_id = pf.id;
  end if;
  delete from public.sale_items where sale_id = p_sale;

  for it in select * from jsonb_array_elements(p_items) loop
    v_qty := (it->>'quantity')::int;
    v_pid := nullif(it->>'perfume_id', '')::uuid;
    if v_pid is not null then
      if v_track then
        update public.perfumes set stock = stock - v_qty where id = v_pid and stock >= v_qty returning * into p;
        if not found then
          raise exception 'insufficient_stock:%', coalesce((select name from public.perfumes where id = v_pid), v_pid::text);
        end if;
      else
        select * into p from public.perfumes where id = v_pid;
        if not found then raise exception 'perfume_not_found'; end if;
      end if;
      v_name := coalesce(nullif(trim(it->>'name'), ''), p.name);
      v_cost := coalesce(nullif(it->>'unit_cost', '')::numeric, p.cost);
    else
      v_name := nullif(trim(it->>'name'), '');
      if v_name is null then raise exception 'item_name_required'; end if;
      v_cost := coalesce(nullif(it->>'unit_cost', '')::numeric, 0);
    end if;
    insert into public.sale_items (sale_id, perfume_id, name, quantity, unit_price, unit_cost)
    values (p_sale, v_pid, v_name, v_qty, (it->>'unit_price')::numeric, v_cost);
  end loop;
end $$;

-- Ajusta el stock de varios perfumes de una vez. Cada ítem: {perfume_id, delta} (sumar/restar) o {perfume_id, set_to}.
-- Todo o nada: si alguno dejaría el stock en negativo, no se aplica ninguno.
create or replace function public.adjust_stock_bulk(p_items jsonb)
returns jsonb language plpgsql security definer set search_path = public as $$
declare it jsonb; p public.perfumes; res jsonb := '[]'::jsonb; v_id uuid;
begin
  for it in select * from jsonb_array_elements(p_items) loop
    v_id := (it->>'perfume_id')::uuid;
    if nullif(it->>'set_to', '') is not null then
      update public.perfumes set stock = (it->>'set_to')::int where id = v_id returning * into p;
    else
      update public.perfumes set stock = stock + (it->>'delta')::int
       where id = v_id and stock + (it->>'delta')::int >= 0 returning * into p;
    end if;
    if not found then
      raise exception 'invalid_stock:%', coalesce((select name from public.perfumes where id = v_id), v_id::text);
    end if;
    res := res || jsonb_build_object('id', p.id, 'stock', p.stock, 'in_stock', p.in_stock);
  end loop;
  return res;
end $$;

revoke all on function public.update_sale(uuid, timestamptz, text, text, text, jsonb) from public, anon, authenticated;
revoke all on function public.adjust_stock_bulk(jsonb) from public, anon, authenticated;
grant execute on function public.update_sale(uuid, timestamptz, text, text, text, jsonb) to service_role;
grant execute on function public.adjust_stock_bulk(jsonb) to service_role;
