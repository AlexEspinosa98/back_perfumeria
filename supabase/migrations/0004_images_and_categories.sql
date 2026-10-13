-- Hasta 3 fotos por perfume + se elimina la categoría "Attar & Oud".
-- Ejecutar en Supabase → SQL Editor después de 0003.

alter table public.perfumes
  add column images text[] not null default '{}'
  check (cardinality(images) <= 3);

-- Los perfumes que ya tenían foto la conservan como foto principal.
update public.perfumes set images = array[image_url]
 where image_url is not null and cardinality(images) = 0;

-- El público puede leer la nueva columna (el costo sigue privado).
grant select (images) on public.perfumes to anon, authenticated;

-- Se quita el filtro "Attar & Oud" (los perfumes quedan; solo pierden esa categoría).
delete from public.categories where slug = 'attar-oud';
