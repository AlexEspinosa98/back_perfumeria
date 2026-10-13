-- Perfumería — esquema inicial (Supabase / Postgres)
-- Ejecutar en el SQL Editor de Supabase o con `supabase db push`.

create extension if not exists "pgcrypto";

-- ───────────────────────── Perfiles (usuarios) ─────────────────────────
create table public.profiles (
  id                  uuid primary key references auth.users(id) on delete cascade,
  email               text not null,
  full_name           text not null default '',
  phone               text not null default '',          -- con código de país, ej. 573001234567
  role                text not null default 'customer' check (role in ('customer', 'admin')),
  -- Habeas data (Ley 1581 de 2012): autorización de tratamiento de datos
  data_consent_at     timestamptz,
  data_consent_version text,
  -- Consentimiento aparte para promociones
  marketing_consent   boolean not null default false,
  unsubscribed_at     timestamptz,
  address             text not null default '',
  city                text not null default '',
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

-- Crea el perfil automáticamente al registrarse (los datos viajan en raw_user_meta_data).
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  consent boolean := coalesce((new.raw_user_meta_data->>'data_consent')::boolean, false);
begin
  insert into public.profiles (id, email, full_name, phone, data_consent_at, data_consent_version, marketing_consent)
  values (
    new.id,
    new.email,
    coalesce(new.raw_user_meta_data->>'full_name', ''),
    regexp_replace(coalesce(new.raw_user_meta_data->>'phone', ''), '\D', '', 'g'),
    case when consent then now() end,
    case when consent then coalesce(new.raw_user_meta_data->>'data_consent_version', 'v1') end,
    coalesce((new.raw_user_meta_data->>'marketing_consent')::boolean, false)
  );
  return new;
end $$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Historial de consentimientos (evidencia ante la SIC)
create table public.consent_log (
  id          bigint generated always as identity primary key,
  user_id     uuid not null references public.profiles(id) on delete cascade,
  kind        text not null check (kind in ('data_treatment', 'marketing')),
  granted     boolean not null,
  version     text,
  created_at  timestamptz not null default now()
);

-- ───────────────────────── Catálogo ─────────────────────────
create table public.categories (
  id          uuid primary key default gen_random_uuid(),
  name        text not null unique,
  slug        text not null unique,
  sort_order  int  not null default 0,
  created_at  timestamptz not null default now()
);

create table public.perfumes (
  id            uuid primary key default gen_random_uuid(),
  name          text not null,
  brand         text not null default '',
  description   text not null default '',
  presentation  text not null default '',          -- ej. "Extrait de Parfum · 100ml"
  price         numeric(12,0) not null check (price >= 0),   -- COP
  image_url     text,
  badge         text,                               -- ej. "EXCLUSIVO", "2X1 PROMO"
  is_active     boolean not null default true,
  is_featured   boolean not null default false,
  in_stock      boolean not null default true,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create table public.perfume_categories (
  perfume_id   uuid not null references public.perfumes(id) on delete cascade,
  category_id  uuid not null references public.categories(id) on delete cascade,
  primary key (perfume_id, category_id)
);

create index perfumes_active_idx on public.perfumes (is_active, created_at desc);
create index perfumes_search_idx on public.perfumes
  using gin (to_tsvector('spanish', name || ' ' || brand || ' ' || description));

-- ───────────────────────── Pedidos ─────────────────────────
create sequence public.order_number_seq start 1001;

create table public.orders (
  id              uuid primary key default gen_random_uuid(),
  order_number    bigint not null unique default nextval('public.order_number_seq'),
  user_id         uuid not null references public.profiles(id),
  status          text not null default 'pending'
                  check (status in ('pending','confirmed','paid','preparing','shipped','delivered','cancelled')),
  total           numeric(12,0) not null default 0,
  shipping_name   text not null default '',
  shipping_phone  text not null default '',
  shipping_address text not null default '',
  shipping_city   text not null default '',
  notes           text not null default '',
  tracking_code   text,
  carrier         text,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create table public.order_items (
  id          uuid primary key default gen_random_uuid(),
  order_id    uuid not null references public.orders(id) on delete cascade,
  perfume_id  uuid references public.perfumes(id) on delete set null,
  name        text not null,                         -- snapshot
  unit_price  numeric(12,0) not null,                -- snapshot
  quantity    int not null check (quantity > 0)
);

-- Seguimiento: una fila por cambio de estado
create table public.order_events (
  id          bigint generated always as identity primary key,
  order_id    uuid not null references public.orders(id) on delete cascade,
  status      text not null,
  note        text not null default '',
  created_by  uuid references public.profiles(id),
  created_at  timestamptz not null default now()
);

create index orders_user_idx on public.orders (user_id, created_at desc);
create index order_events_order_idx on public.order_events (order_id, created_at);

-- ───────────────────────── Comunicaciones ─────────────────────────
create table public.email_templates (
  id          uuid primary key default gen_random_uuid(),
  name        text not null unique,
  subject     text not null,
  html        text not null,                         -- admite {{name}}, {{unsubscribe_url}}, ...
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table public.whatsapp_templates (
  id          uuid primary key default gen_random_uuid(),
  name        text not null unique,
  body        text not null,                         -- admite {{name}}, {{order_number}}, {{status}}, {{tracking}}
  created_at  timestamptz not null default now()
);

create table public.campaigns (
  id            uuid primary key default gen_random_uuid(),
  name          text not null,
  template_id   uuid not null references public.email_templates(id),
  status        text not null default 'draft' check (status in ('draft','sending','sent')),
  created_at    timestamptz not null default now(),
  sent_at       timestamptz
);

create table public.campaign_recipients (
  id           bigint generated always as identity primary key,
  campaign_id  uuid not null references public.campaigns(id) on delete cascade,
  user_id      uuid not null references public.profiles(id) on delete cascade,
  email        text not null,
  status       text not null default 'pending' check (status in ('pending','sent','failed','skipped')),
  error        text,
  sent_at      timestamptz,
  unique (campaign_id, user_id)
);

create index campaign_recipients_pending_idx on public.campaign_recipients (campaign_id, status);

-- Configuración de la tienda (número de WhatsApp, etc.)
create table public.store_settings (
  key    text primary key,
  value  text not null
);
insert into public.store_settings (key, value) values
  ('whatsapp_number', ''),
  ('store_name', 'Bahía Perfumes'),
  ('announcement', '');

-- ───────────────────────── updated_at automático ─────────────────────────
create or replace function public.touch_updated_at() returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end $$;

create trigger profiles_touch  before update on public.profiles  for each row execute function public.touch_updated_at();
create trigger perfumes_touch  before update on public.perfumes  for each row execute function public.touch_updated_at();
create trigger orders_touch    before update on public.orders    for each row execute function public.touch_updated_at();
create trigger email_tpl_touch before update on public.email_templates for each row execute function public.touch_updated_at();

-- ───────────────────────── RLS ─────────────────────────
create or replace function public.is_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.profiles where id = auth.uid() and role = 'admin');
$$;

alter table public.profiles            enable row level security;
alter table public.consent_log         enable row level security;
alter table public.categories          enable row level security;
alter table public.perfumes            enable row level security;
alter table public.perfume_categories  enable row level security;
alter table public.orders              enable row level security;
alter table public.order_items         enable row level security;
alter table public.order_events        enable row level security;
alter table public.email_templates     enable row level security;
alter table public.whatsapp_templates  enable row level security;
alter table public.campaigns           enable row level security;
alter table public.campaign_recipients enable row level security;
alter table public.store_settings      enable row level security;

-- profiles: cada quien ve y edita lo suyo (sin poder cambiar su rol); admin ve todo
create policy profiles_select on public.profiles for select using (id = auth.uid() or public.is_admin());
create policy profiles_update on public.profiles for update
  using (id = auth.uid())
  with check (id = auth.uid() and role = (select role from public.profiles where id = auth.uid()));
create policy profiles_admin_all on public.profiles for all using (public.is_admin()) with check (public.is_admin());

create policy consent_select on public.consent_log for select using (user_id = auth.uid() or public.is_admin());

-- catálogo: lectura pública, escritura solo admin
create policy categories_read on public.categories for select using (true);
create policy categories_admin on public.categories for all using (public.is_admin()) with check (public.is_admin());
create policy perfumes_read on public.perfumes for select using (is_active or public.is_admin());
create policy perfumes_admin on public.perfumes for all using (public.is_admin()) with check (public.is_admin());
create policy perfume_cat_read on public.perfume_categories for select using (true);
create policy perfume_cat_admin on public.perfume_categories for all using (public.is_admin()) with check (public.is_admin());

-- pedidos: el cliente ve los suyos; los cambios pasan por la API (service role)
create policy orders_select on public.orders for select using (user_id = auth.uid() or public.is_admin());
create policy order_items_select on public.order_items for select
  using (exists (select 1 from public.orders o where o.id = order_id and (o.user_id = auth.uid() or public.is_admin())));
create policy order_events_select on public.order_events for select
  using (exists (select 1 from public.orders o where o.id = order_id and (o.user_id = auth.uid() or public.is_admin())));

-- comunicaciones y ajustes: solo admin (la API usa service role)
create policy email_tpl_admin on public.email_templates for all using (public.is_admin()) with check (public.is_admin());
create policy wa_tpl_admin on public.whatsapp_templates for all using (public.is_admin()) with check (public.is_admin());
create policy campaigns_admin on public.campaigns for all using (public.is_admin()) with check (public.is_admin());
create policy campaign_rcpt_admin on public.campaign_recipients for all using (public.is_admin()) with check (public.is_admin());
create policy settings_read on public.store_settings for select using (key in ('whatsapp_number', 'store_name', 'announcement'));
create policy settings_admin on public.store_settings for all using (public.is_admin()) with check (public.is_admin());

-- ───────────────────────── Storage: imágenes de perfumes ─────────────────────────
insert into storage.buckets (id, name, public) values ('perfumes', 'perfumes', true)
on conflict (id) do nothing;

create policy "perfumes_images_read" on storage.objects for select using (bucket_id = 'perfumes');
create policy "perfumes_images_admin" on storage.objects for all
  using (bucket_id = 'perfumes' and public.is_admin())
  with check (bucket_id = 'perfumes' and public.is_admin());

-- ───────────────────────── Semillas ─────────────────────────
insert into public.categories (name, slug, sort_order) values
  ('Hombres', 'hombres', 1), ('Mujeres', 'mujeres', 2), ('Unisex', 'unisex', 3),
  ('Promociones', 'promociones', 5), ('Nuevos', 'nuevos', 6);

insert into public.whatsapp_templates (name, body) values
  ('Confirmación de pedido', 'Hola {{name}} 👋, soy de {{store}}. Recibí tu pedido #{{order_number}} por {{total}}. ¿Confirmamos dirección de envío y forma de pago?'),
  ('Pedido enviado', 'Hola {{name}}, tu pedido #{{order_number}} ya va en camino 🚚. Guía: {{tracking}}.'),
  ('Promoción', 'Hola {{name}} ✨, esta semana tenemos una promoción especial en {{store}}. ¿Te cuento los detalles?');

insert into public.email_templates (name, subject, html) values
  ('Promoción', '{{name}}, una promoción para ti',
   '<div style="font-family:Georgia,serif;max-width:560px;margin:auto"><h1>Hola {{name}}</h1><p>Tenemos novedades en nuestra colección.</p><p><a href="{{shop_url}}">Ver catálogo</a></p><hr><p style="font-size:12px;color:#777"><a href="{{unsubscribe_url}}">Dejar de recibir promociones</a></p></div>'),
  ('Estado de pedido', 'Tu pedido #{{order_number}}: {{status}}',
   '<div style="font-family:Georgia,serif;max-width:560px;margin:auto"><h1>Hola {{name}}</h1><p>Tu pedido <b>#{{order_number}}</b> ahora está: <b>{{status}}</b>.</p><p>{{note}}</p></div>');

-- Para volver admin a alguien (después de que se registre):
--   update public.profiles set role = 'admin' where email = 'tu-correo@ejemplo.com';
