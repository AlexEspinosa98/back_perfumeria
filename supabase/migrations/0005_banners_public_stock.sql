-- Carrusel del home administrable (imágenes especiales) + unidades visibles en el catálogo.
-- Ejecutar en Supabase → SQL Editor después de 0004.

create table public.banners (
  id                uuid primary key default gen_random_uuid(),
  image_url         text not null,                    -- imagen principal (escritorio)
  mobile_image_url  text,                             -- imagen para celular (opcional)
  title             text not null default '',
  subtitle          text not null default '',
  button_label      text not null default '',
  link_url          text not null default '',         -- ruta interna (/colecciones) o enlace externo
  is_active         boolean not null default true,
  sort_order        int not null default 0,
  created_at        timestamp with time zone not null default now()
);
create index banners_active_idx on public.banners (is_active, sort_order, created_at desc);

alter table public.banners enable row level security;
create policy banners_read on public.banners for select using (is_active or public.is_admin());
create policy banners_admin on public.banners for all using (public.is_admin()) with check (public.is_admin());

-- Las unidades disponibles ahora se muestran al público (el costo sigue privado).
grant select (stock) on public.perfumes to anon, authenticated;
