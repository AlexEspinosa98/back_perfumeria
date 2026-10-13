from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import require_admin
from app.core.supabase import get_db
from app.schemas import BannerIn, BannerUpdate

public_router = APIRouter(tags=["carrusel"])
admin_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _check_link(link: str | None) -> str | None:
    """El enlace del botón debe ser una ruta interna (/algo) o http(s)://. Evita javascript: y similares."""
    if link is None or link == "":
        return link
    link = link.strip()
    if link.startswith("/") and not link.startswith("//"):
        return link
    if link.lower().startswith(("https://", "http://")):
        return link
    raise HTTPException(422, "El enlace debe empezar con / (página de la tienda) o con https://")


def _check_img(url: str | None) -> str | None:
    if url and not url.lower().startswith(("https://", "http://")):
        raise HTTPException(422, "La imagen debe ser una URL http(s)")
    return url


@public_router.get("/banners")
def list_banners():
    """Slides activas del carrusel del home, en el orden que definió el admin."""
    return (get_db().table("banners")
            .select("id, image_url, mobile_image_url, title, subtitle, button_label, link_url")
            .eq("is_active", True).order("sort_order").order("created_at", desc=True).execute().data)


@admin_router.get("/banners")
def admin_banners():
    return get_db().table("banners").select("*").order("sort_order").order("created_at", desc=True).execute().data


@admin_router.post("/banners", status_code=201)
def create_banner(body: BannerIn):
    data = body.model_dump()
    data["link_url"] = _check_link(data["link_url"]) or ""
    data["image_url"] = _check_img(data["image_url"])
    data["mobile_image_url"] = _check_img(data["mobile_image_url"]) or None
    return get_db().table("banners").insert(data).execute().data[0]


@admin_router.patch("/banners/{banner_id}")
def update_banner(banner_id: str, body: BannerUpdate):
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(422, "Nada que actualizar")
    if "link_url" in data:
        data["link_url"] = _check_link(data["link_url"]) or ""
    if "image_url" in data:
        data["image_url"] = _check_img(data["image_url"])
    if "mobile_image_url" in data:
        data["mobile_image_url"] = _check_img(data["mobile_image_url"]) or None
    rows = get_db().table("banners").update(data).eq("id", banner_id).execute().data
    if not rows:
        raise HTTPException(404, "Banner no encontrado")
    return rows[0]


@admin_router.delete("/banners/{banner_id}", status_code=204)
def delete_banner(banner_id: str):
    get_db().table("banners").delete().eq("id", banner_id).execute()
