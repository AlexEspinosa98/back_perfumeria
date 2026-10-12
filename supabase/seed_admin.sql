-- Crea (o actualiza) un usuario administrador. Ejecutar en Supabase → SQL Editor.
-- 1) Cambia los dos valores de abajo ANTES de ejecutar. 2) No subas este archivo con la clave real a git.
do $$
declare
  v_email    text := 'CAMBIA_ESTE_CORREO';
  v_password text := 'CAMBIA_ESTA_CLAVE';
  v_id       uuid;
begin
  select id into v_id from auth.users where email = v_email;

  if v_id is null then
    v_id := gen_random_uuid();
    insert into auth.users (
      instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
      raw_app_meta_data, raw_user_meta_data, created_at, updated_at,
      confirmation_token, email_change, email_change_token_new, recovery_token
    ) values (
      '00000000-0000-0000-0000-000000000000', v_id, 'authenticated', 'authenticated', v_email,
      crypt(v_password, gen_salt('bf')), now(),
      '{"provider":"email","providers":["email"]}',
      '{"full_name":"Administrador","phone":"3000000000","data_consent":true,"data_consent_version":"v1"}',
      now(), now(), '', '', '', ''
    );
    insert into auth.identities (id, user_id, provider_id, identity_data, provider, last_sign_in_at, created_at, updated_at)
    values (gen_random_uuid(), v_id, v_id::text,
            jsonb_build_object('sub', v_id::text, 'email', v_email, 'email_verified', true),
            'email', now(), now(), now());
  else
    update auth.users set encrypted_password = crypt(v_password, gen_salt('bf')),
                          email_confirmed_at = coalesce(email_confirmed_at, now()), updated_at = now()
    where id = v_id;
  end if;

  -- El trigger on_auth_user_created ya creó el perfil; aquí se vuelve admin y se registra el consentimiento.
  update public.profiles
     set role = 'admin',
         data_consent_at = coalesce(data_consent_at, now()),
         data_consent_version = coalesce(data_consent_version, 'v1')
   where id = v_id;
end $$;

-- Marca de la tienda (si ya habías ejecutado 0001 con el nombre anterior):
insert into public.store_settings (key, value) values ('store_name', 'Bahía Perfumes'), ('announcement', '')
on conflict (key) do update set value = excluded.value where public.store_settings.key = 'store_name';
