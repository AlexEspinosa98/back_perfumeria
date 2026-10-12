from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.core.supabase import get_db
from app.services.email import sign_unsubscribe

router = APIRouter(tags=["public"])

PERFUME_SELECT = "*, categories(id, name, slug)"


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/categories")
def list_categories():
    return get_db().table("categories").select("*").order("sort_order").execute().data


@router.get("/perfumes")
def list_perfumes(
    q: str | None = None,
    category: str | None = Query(None, description="slug de categoría"),
    featured: bool | None = None,
    limit: int = Query(24, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    db = get_db()
    query = db.table("perfumes").select(PERFUME_SELECT, count="exact").eq("is_active", True)
    if category:
        cat = db.table("categories").select("id").eq("slug", category).limit(1).execute().data
        if not cat:
            return {"items": [], "total": 0}
        ids = [r["perfume_id"] for r in
               db.table("perfume_categories").select("perfume_id").eq("category_id", cat[0]["id"]).execute().data]
        if not ids:
            return {"items": [], "total": 0}
        query = query.in_("id", ids)
    if q:
        term = q.replace(",", " ").replace("%", " ").strip()
        query = query.or_(f"name.ilike.%{term}%,brand.ilike.%{term}%,description.ilike.%{term}%")
    if featured is not None:
        query = query.eq("is_featured", featured)
    res = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    return {"items": res.data, "total": res.count}


@router.get("/perfumes/{perfume_id}")
def get_perfume(perfume_id: str):
    rows = (get_db().table("perfumes").select(PERFUME_SELECT)
            .eq("id", perfume_id).eq("is_active", True).limit(1).execute().data)
    if not rows:
        raise HTTPException(404, "Perfume no encontrado")
    return rows[0]


@router.get("/store")
def store_info():
    """Datos públicos de la tienda: nombre y WhatsApp para el botón 'Pedir por WhatsApp'."""
    rows = get_db().table("store_settings").select("*").in_("key", ["whatsapp_number", "store_name"]).execute().data
    return {r["key"]: r["value"] for r in rows}


@router.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe(uid: str, sig: str):
    """Enlace de baja de promociones incluido en cada correo masivo."""
    import hmac

    if not hmac.compare_digest(sig, sign_unsubscribe(uid)):
        raise HTTPException(400, "Enlace inválido")
    get_db().table("profiles").update(
        {"marketing_consent": False, "unsubscribed_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", uid).execute()
    get_db().table("consent_log").insert(
        {"user_id": uid, "kind": "marketing", "granted": False, "version": "unsubscribe-link"}
    ).execute()
    return "<h2 style='font-family:Georgia,serif;text-align:center;margin-top:20vh'>Listo, no recibirás más promociones.</h2>"
