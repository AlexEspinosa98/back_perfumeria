-- Reseñas de clientes (con moderación) y testimonios (fotos/capturas que llegan por WhatsApp).
-- Ejecutar en Supabase → SQL Editor después de 0001.

create table public.reviews (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references public.profiles(id) on delete cascade,
  perfume_id  uuid not null references public.perfumes(id) on delete cascade,
  rating      int  not null check (rating between 1 and 5),
  comment     text not null check (char_length(comment) between 10 and 1000),
  status      text not null default 'pending' check (status in ('pending','approved','rejected')),
  created_at  timestamptz not null default now(),
  unique (user_id, perfume_id)
);
create index reviews_perfume_idx on public.reviews (perfume_id, status, created_at desc);
create index reviews_status_idx  on public.reviews (status, created_at desc);

create table public.testimonials (
  id          uuid primary key default gen_random_uuid(),
  image_url   text not null,
  author      text not null default '',          -- ej. "Sofía R., Bogotá" (sin teléfono)
  caption     text not null default '',          -- comentario que acompaña la foto (opcional)
  is_active   boolean not null default true,
  sort_order  int not null default 0,
  created_at  timestamptz not null default now()
);
create index testimonials_active_idx on public.testimonials (is_active, sort_order, created_at desc);

alter table public.reviews      enable row level security;
alter table public.testimonials enable row level security;

create policy reviews_read_approved on public.reviews for select using (status = 'approved' or user_id = auth.uid() or public.is_admin());
create policy reviews_admin on public.reviews for all using (public.is_admin()) with check (public.is_admin());
create policy testimonials_read on public.testimonials for select using (is_active or public.is_admin());
create policy testimonials_admin on public.testimonials for all using (public.is_admin()) with check (public.is_admin());

-- Datos de contacto de la tienda
insert into public.store_settings (key, value) values
  ('whatsapp_number', '573218137847'),
  ('instagram_url',   'https://instagram.com/bahiaperfumes.co'),
  ('facebook_url',    'https://www.facebook.com/people/Bahia-Perfumes/61573514763443/'),
  ('store_name',      'Bahía Perfumes')
on conflict (key) do update set value = excluded.value;

drop policy if exists settings_read on public.store_settings;
create policy settings_read on public.store_settings for select
  using (key in ('whatsapp_number', 'store_name', 'announcement', 'instagram_url', 'facebook_url'));
