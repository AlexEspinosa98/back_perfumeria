# Perfumería — Backend (FastAPI + Supabase)

API para la tienda: catálogo público, clientes (registro, perfil, consentimientos, pedidos y seguimiento) y panel admin (catálogo, pedidos, correos, plantillas de WhatsApp).
Supabase aporta Auth, Postgres (con RLS) y Storage. El front **no habla con Supabase**: todo (incluido el login) pasa por esta API, que usa Supabase por detrás.

## Puesta en marcha

1. **Supabase**: crear proyecto → SQL Editor → ejecutar `supabase/migrations/0001_init.sql`.
2. Auth → Providers → Email activado. `POST /auth/register` envía los datos y consentimientos a Supabase y un trigger crea el perfil.
   Auth → URL Configuration: *Site URL* = URL del front y agrega `https://TU-FRONT/**` en Redirect URLs (los correos de confirmar/recuperar redirigen allí).
3. Registrarte en el sitio y volverte admin:
   ```sql
   update public.profiles set role = 'admin' where email = 'tu-correo@ejemplo.com';
   ```
4. `cp .env.example .env` y completar (URL, anon key, service role key).
5. Local:
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   uvicorn app.main:app --reload     # docs en /docs
   ```
6. Vercel: importar este repo, cargar las mismas variables de `.env.example`. `CORS_ORIGINS` = URL del front.
7. Panel admin → `PUT /admin/settings/whatsapp_number` con tu número (ej. `573001234567`).

## Endpoints principales

| Área | Rutas |
|---|---|
| Auth | `POST /auth/register`, `/auth/login`, `/auth/refresh`, `/auth/logout`, `/auth/forgot-password`, `/auth/reset-password` |
| Público | `GET /perfumes?q=&category=&featured=`, `GET /perfumes/{id}`, `GET /categories`, `GET /store`, `GET /unsubscribe` |
| Cliente (Bearer) | `GET/PATCH /me`, `POST /me/consents`, `GET /me/orders`, `GET /me/orders/{id}` (con línea de tiempo), `POST /me/orders` (devuelve `whatsapp_url` para coordinar con la tienda) |
| Admin (Bearer, rol admin) | `/admin/perfumes`, `/admin/categories`, `/admin/uploads/perfume-image`, `/admin/orders` + `PATCH …/status`, `/admin/customers`, `/admin/templates/{email,whatsapp}`, `/admin/emails/send`, `/admin/campaigns` + `POST …/send` |

## WhatsApp (sin API)
`GET /admin/orders/{id}/whatsapp?template_id=` y `GET /admin/customers/{id}/whatsapp?template_id=` devuelven un enlace `wa.me` con la plantilla llenada. El front lo abre y WhatsApp queda listo en tu cuenta personal; tú pulsas enviar. No hay envío masivo automático (eso requeriría la API de WhatsApp Business).

## Correos
Resend. Sin `RESEND_API_KEY` los correos no se envían (se registra en log). Cuando tengas dominio: verificarlo en Resend (SPF/DKIM/DMARC) y poner `EMAIL_FROM`.
Campañas masivas: `POST /admin/campaigns` fija los destinatarios (solo `marketing_consent` y sin baja); `POST /admin/campaigns/{id}/send` envía hasta 50 por llamada — repetir hasta `remaining: 0` (cada request cabe en el límite de Vercel). Cada correo lleva enlace de baja (`{{unsubscribe_url}}`).

## Habeas data (Ley 1581)
El consentimiento de datos se guarda con fecha y versión (`profiles`, `consent_log`); sin él no se puede hacer pedidos. Promociones es un consentimiento aparte y revocable (`POST /me/consents` o enlace de baja).
