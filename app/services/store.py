from app.core.supabase import get_db

PUBLIC_KEYS = ["whatsapp_number", "store_name", "announcement", "instagram_url", "facebook_url"]

# Valores por defecto: el sitio funciona aunque la tabla store_settings aún no tenga estas filas.
DEFAULTS = {
    "whatsapp_number": "573218137847",
    "store_name": "Bahía Perfumes",
    "announcement": "",
    "instagram_url": "https://instagram.com/bahiaperfumes.co",
    "facebook_url": "https://www.facebook.com/people/Bahia-Perfumes/61573514763443/",
}


def get_store() -> dict:
    """Ajustes públicos: lo guardado en la BD (si no está vacío) sobre los valores por defecto."""
    rows = get_db().table("store_settings").select("*").in_("key", PUBLIC_KEYS).execute().data
    out = dict(DEFAULTS)
    out.update({r["key"]: r["value"] for r in rows if r["value"] != "" or r["key"] == "announcement"})
    return out
