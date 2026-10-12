import re
import unicodedata
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile

from app.core.deps import CurrentUser, require_admin
from app.core.supabase import get_db
from app.schemas import (
    ORDER_STATUSES, STATUS_LABELS, CampaignIn, CategoryIn, EmailTemplateIn, OrderStatusUpdate,
    PerfumeIn, PerfumeUpdate, SendIndividualIn, SettingIn, WhatsappTemplateIn,
)
from app.services import email as mail
from app.services.store import get_store
from app.services.templating import render
from app.services.whatsapp import wa_link
from app.core.config import settings

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def slugify(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def store_values() -> dict:
    return get_store()


# ───────────────────────── Categorías ─────────────────────────
@router.post("/categories", status_code=201)
def create_category(body: CategoryIn):
    return get_db().table("categories").insert(
        {"name": body.name, "slug": body.slug or slugify(body.name), "sort_order": body.sort_order}
    ).execute().data[0]


@router.put("/categories/{category_id}")
def update_category(category_id: str, body: CategoryIn):
    return get_db().table("categories").update(
        {"name": body.name, "slug": body.slug or slugify(body.name), "sort_order": body.sort_order}
    ).eq("id", category_id).execute().data[0]


@router.delete("/categories/{category_id}", status_code=204)
def delete_category(category_id: str):
    get_db().table("categories").delete().eq("id", category_id).execute()


# ───────────────────────── Catálogo ─────────────────────────
def _set_categories(perfume_id: str, category_ids: list[str]) -> None:
    db = get_db()
    db.table("perfume_categories").delete().eq("perfume_id", perfume_id).execute()
    if category_ids:
        db.table("perfume_categories").insert(
            [{"perfume_id": perfume_id, "category_id": c} for c in category_ids]
        ).execute()


@router.get("/perfumes")
def admin_list_perfumes(q: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    """Incluye perfumes inactivos."""
    query = get_db().table("perfumes").select("*, categories(id, name, slug)", count="exact")
    if q:
        term = q.replace(",", " ").replace("%", " ").strip()
        query = query.or_(f"name.ilike.%{term}%,brand.ilike.%{term}%")
    res = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    return {"items": res.data, "total": res.count}


@router.post("/perfumes", status_code=201)
def create_perfume(body: PerfumeIn):
    data = body.model_dump(exclude={"category_ids"})
    perfume = get_db().table("perfumes").insert(data).execute().data[0]
    _set_categories(perfume["id"], body.category_ids)
    return perfume


@router.patch("/perfumes/{perfume_id}")
def update_perfume(perfume_id: str, body: PerfumeUpdate):
    data = body.model_dump(exclude_unset=True, exclude={"category_ids"})
    perfume = None
    if data:
        rows = get_db().table("perfumes").update(data).eq("id", perfume_id).execute().data
        if not rows:
            raise HTTPException(404, "Perfume no encontrado")
        perfume = rows[0]
    if body.category_ids is not None:
        _set_categories(perfume_id, body.category_ids)
    return perfume or get_db().table("perfumes").select("*").eq("id", perfume_id).execute().data[0]


@router.delete("/perfumes/{perfume_id}", status_code=204)
def delete_perfume(perfume_id: str):
    get_db().table("perfumes").delete().eq("id", perfume_id).execute()


@router.post("/uploads/perfume-image")
async def upload_perfume_image(file: UploadFile = File(...)):
    """Sube la imagen al bucket público 'perfumes' y devuelve la URL para guardarla en image_url."""
    ext = IMAGE_TYPES.get(file.content_type or "")
    if not ext:
        raise HTTPException(415, "Solo JPG, PNG o WEBP")
    content = await file.read()
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "La imagen supera 5 MB")
    path = f"{uuid.uuid4().hex}.{ext}"
    bucket = get_db().storage.from_("perfumes")
    bucket.upload(path, content, {"content-type": file.content_type})
    return {"url": bucket.get_public_url(path), "path": path}


# ───────────────────────── Pedidos ─────────────────────────
@router.get("/orders")
def admin_orders(status: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    query = get_db().table("orders").select("*, order_items(*), profiles(full_name, email, phone)", count="exact")
    if status:
        query = query.eq("status", status)
    res = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    return {"items": res.data, "total": res.count}


@router.get("/orders/pending-count")
def pending_count():
    """Pedidos sin atender (estado pendiente): para el contador del panel."""
    return {"pending": get_db().table("orders").select("id", count="exact").eq("status", "pending").execute().count}


@router.get("/orders/{order_id}")
def admin_order(order_id: str):
    db = get_db()
    rows = (db.table("orders").select("*, order_items(*), profiles(full_name, email, phone)")
            .eq("id", order_id).limit(1).execute().data)
    if not rows:
        raise HTTPException(404, "Pedido no encontrado")
    order = rows[0]
    order["events"] = db.table("order_events").select("*").eq("order_id", order_id).order("created_at").execute().data
    return order


@router.patch("/orders/{order_id}/status")
def update_order_status(order_id: str, body: OrderStatusUpdate, admin: CurrentUser = Depends(require_admin)):
    """Cambia el estado, registra el evento de seguimiento y (opcional) avisa por correo al cliente."""
    if body.status not in ORDER_STATUSES:
        raise HTTPException(422, f"Estado inválido. Opciones: {ORDER_STATUSES}")
    db = get_db()
    current = db.table("orders").select("id, status").eq("id", order_id).limit(1).execute().data
    if not current:
        raise HTTPException(404, "Pedido no encontrado")
    old_status = current[0]["status"]

    # Stock: cancelar devuelve las unidades; reabrir un pedido cancelado las vuelve a apartar.
    try:
        if body.status == "cancelled" and old_status != "cancelled":
            db.rpc("set_order_stock", {"p_order": order_id, "p_reserve": False}).execute()
        elif old_status == "cancelled" and body.status != "cancelled":
            db.rpc("set_order_stock", {"p_order": order_id, "p_reserve": True}).execute()
    except Exception as exc:
        msg = str(getattr(exc, "message", None) or exc)
        if "insufficient_stock:" in msg:
            raise HTTPException(409, "No hay stock suficiente para reabrir este pedido")
        raise HTTPException(400, "No se pudo ajustar el stock del pedido")

    patch = {"status": body.status}
    if body.tracking_code is not None:
        patch["tracking_code"] = body.tracking_code
    if body.carrier is not None:
        patch["carrier"] = body.carrier
    rows = db.table("orders").update(patch).eq("id", order_id).execute().data
    order = rows[0]
    db.table("order_events").insert(
        {"order_id": order_id, "status": body.status, "note": body.note, "created_by": admin.id}
    ).execute()

    # Contabilidad: pagado o entregado = venta registrada (una sola vez); cancelado = se anula la venta.
    sale_registered = False
    if body.status in ("paid", "delivered"):
        sale_registered = bool(db.rpc("sale_from_order", {"p_order": order_id, "p_by": admin.id}).execute().data)
    elif body.status == "cancelled":
        db.table("sales").delete().eq("order_id", order_id).execute()

    email_result = None
    if body.notify_email:
        cust = db.table("profiles").select("full_name, email").eq("id", order["user_id"]).execute().data[0]
        tpl = db.table("email_templates").select("*").eq("name", "Estado de pedido").limit(1).execute().data
        if tpl:
            vals = {"name": cust["full_name"] or "cliente", "order_number": order["order_number"],
                    "status": STATUS_LABELS[body.status], "note": body.note}
            ok, err = mail.send_one(cust["email"], render(tpl[0]["subject"], vals),
                                    render(tpl[0]["html"], vals, escape=True))
            email_result = {"sent": ok, "error": err}
    return {"order": order, "email": email_result, "sale_registered": sale_registered}


@router.get("/orders/{order_id}/whatsapp")
def order_whatsapp(order_id: str, template_id: str):
    """Enlace wa.me con la plantilla llenada: al abrirlo, WhatsApp queda listo en TU cuenta para enviar."""
    db = get_db()
    rows = (db.table("orders").select("*, profiles(full_name, phone)").eq("id", order_id).limit(1).execute().data)
    tpl = db.table("whatsapp_templates").select("*").eq("id", template_id).limit(1).execute().data
    if not rows or not tpl:
        raise HTTPException(404, "Pedido o plantilla no encontrados")
    o = rows[0]
    cust = o["profiles"]
    total = f"${int(o['total']):,}".replace(",", ".")
    vals = {"name": cust["full_name"] or o["shipping_name"], "order_number": o["order_number"],
            "status": STATUS_LABELS[o["status"]], "tracking": o["tracking_code"] or "pendiente",
            "total": total, "store": store_values().get("store_name", "")}
    message = render(tpl[0]["body"], vals)
    return {"url": wa_link(o["shipping_phone"] or cust["phone"], message), "message": message}


# ───────────────────────── Clientes ─────────────────────────
@router.get("/customers")
def customers(q: str | None = None, marketing_only: bool = False,
              limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    query = get_db().table("profiles").select("*", count="exact").eq("role", "customer")
    if marketing_only:
        query = query.eq("marketing_consent", True)
    if q:
        term = q.replace(",", " ").replace("%", " ").strip()
        query = query.or_(f"full_name.ilike.%{term}%,email.ilike.%{term}%,phone.ilike.%{term}%")
    res = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    return {"items": res.data, "total": res.count}


@router.get("/customers/{user_id}/whatsapp")
def customer_whatsapp(user_id: str, template_id: str):
    """Mensaje individual (ej. promoción) al WhatsApp del cliente, enviado desde tu cuenta."""
    db = get_db()
    cust = db.table("profiles").select("*").eq("id", user_id).limit(1).execute().data
    tpl = db.table("whatsapp_templates").select("*").eq("id", template_id).limit(1).execute().data
    if not cust or not tpl:
        raise HTTPException(404, "Cliente o plantilla no encontrados")
    c = cust[0]
    message = render(tpl[0]["body"], {"name": c["full_name"] or "", "store": store_values().get("store_name", "")})
    return {"url": wa_link(c["phone"], message), "message": message}


# ───────────────────────── Plantillas ─────────────────────────
@router.get("/templates/email")
def list_email_templates():
    return get_db().table("email_templates").select("*").order("name").execute().data


@router.post("/templates/email", status_code=201)
def create_email_template(body: EmailTemplateIn):
    return get_db().table("email_templates").insert(body.model_dump()).execute().data[0]


@router.put("/templates/email/{template_id}")
def update_email_template(template_id: str, body: EmailTemplateIn):
    return get_db().table("email_templates").update(body.model_dump()).eq("id", template_id).execute().data[0]


@router.delete("/templates/email/{template_id}", status_code=204)
def delete_email_template(template_id: str):
    get_db().table("email_templates").delete().eq("id", template_id).execute()


@router.get("/templates/whatsapp")
def list_whatsapp_templates():
    return get_db().table("whatsapp_templates").select("*").order("name").execute().data


@router.post("/templates/whatsapp", status_code=201)
def create_whatsapp_template(body: WhatsappTemplateIn):
    return get_db().table("whatsapp_templates").insert(body.model_dump()).execute().data[0]


@router.put("/templates/whatsapp/{template_id}")
def update_whatsapp_template(template_id: str, body: WhatsappTemplateIn):
    return get_db().table("whatsapp_templates").update(body.model_dump()).eq("id", template_id).execute().data[0]


@router.delete("/templates/whatsapp/{template_id}", status_code=204)
def delete_whatsapp_template(template_id: str):
    get_db().table("whatsapp_templates").delete().eq("id", template_id).execute()


# ───────────────────────── Correos ─────────────────────────
def _email_for(profile: dict, tpl: dict, api_base: str) -> dict:
    vals = {"name": profile["full_name"] or "", "shop_url": settings.FRONTEND_URL,
            "unsubscribe_url": mail.unsubscribe_url(profile["id"], api_base)}
    return {"to": profile["email"], "subject": render(tpl["subject"], vals),
            "html": render(tpl["html"], vals, escape=True)}


@router.post("/emails/send")
def send_individual_email(body: SendIndividualIn, request: Request):
    """Correo individual con plantilla a un cliente."""
    db = get_db()
    prof = db.table("profiles").select("*").eq("id", body.user_id).limit(1).execute().data
    tpl = db.table("email_templates").select("*").eq("id", body.template_id).limit(1).execute().data
    if not prof or not tpl:
        raise HTTPException(404, "Cliente o plantilla no encontrados")
    msg = _email_for(prof[0], tpl[0], str(request.base_url))
    ok, err = mail.send_one(msg["to"], msg["subject"], msg["html"])
    return {"sent": ok, "error": err}


@router.post("/campaigns", status_code=201)
def create_campaign(body: CampaignIn):
    """Crea la campaña y fija los destinatarios: solo clientes con promociones aceptadas y sin baja."""
    db = get_db()
    campaign = db.table("campaigns").insert({"name": body.name, "template_id": body.template_id}).execute().data[0]
    query = (db.table("profiles").select("id, email").eq("role", "customer")
             .eq("marketing_consent", True).is_("unsubscribed_at", "null"))
    if body.only_user_ids is not None:
        query = query.in_("id", body.only_user_ids)
    recipients = query.execute().data
    if recipients:
        db.table("campaign_recipients").insert(
            [{"campaign_id": campaign["id"], "user_id": r["id"], "email": r["email"]} for r in recipients]
        ).execute()
    return {"campaign": campaign, "recipients": len(recipients)}


@router.get("/campaigns")
def list_campaigns():
    return get_db().table("campaigns").select("*, email_templates(name)").order("created_at", desc=True).execute().data


@router.get("/campaigns/{campaign_id}")
def campaign_detail(campaign_id: str):
    db = get_db()
    rows = db.table("campaign_recipients").select("status").eq("campaign_id", campaign_id).execute().data
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"id": campaign_id, "counts": counts, "total": len(rows)}


@router.post("/campaigns/{campaign_id}/send")
def send_campaign_chunk(campaign_id: str, request: Request, chunk: int = Query(50, ge=1, le=100)):
    """Envía hasta `chunk` pendientes. Llámalo repetidamente hasta que `remaining` sea 0
    (así cada request cabe en el límite de tiempo de Vercel)."""
    db = get_db()
    camp = db.table("campaigns").select("*, email_templates(*)").eq("id", campaign_id).limit(1).execute().data
    if not camp:
        raise HTTPException(404, "Campaña no encontrada")
    if not mail.email_enabled():
        raise HTTPException(503, "Correo no configurado (falta RESEND_API_KEY)")
    tpl = camp[0]["email_templates"]

    pending = (db.table("campaign_recipients").select("id, user_id")
               .eq("campaign_id", campaign_id).eq("status", "pending").limit(chunk).execute().data)
    if pending:
        db.table("campaigns").update({"status": "sending"}).eq("id", campaign_id).execute()
        profs = {p["id"]: p for p in db.table("profiles").select("*")
                 .in_("id", [r["user_id"] for r in pending]).execute().data}
        base = str(request.base_url)
        # Re-verifica el consentimiento justo antes de enviar (puede haber cambiado desde la creación).
        eligible = [r for r in pending
                    if profs.get(r["user_id"], {}).get("marketing_consent")
                    and not profs[r["user_id"]].get("unsubscribed_at")]
        skipped = [r["id"] for r in pending if r not in eligible]
        if skipped:
            db.table("campaign_recipients").update({"status": "skipped"}).in_("id", skipped).execute()
        if eligible:
            ok, err = mail.send_batch([_email_for(profs[r["user_id"]], tpl, base) for r in eligible])
            ids = [r["id"] for r in eligible]
            db.table("campaign_recipients").update(
                {"status": "sent" if ok else "failed", "error": err, "sent_at": now_iso() if ok else None}
            ).in_("id", ids).execute()

    remaining = (db.table("campaign_recipients").select("id", count="exact")
                 .eq("campaign_id", campaign_id).eq("status", "pending").execute().count)
    if remaining == 0:
        db.table("campaigns").update({"status": "sent", "sent_at": now_iso()}).eq("id", campaign_id).execute()
    return {"processed": len(pending), "remaining": remaining}


# ───────────────────────── Ajustes de tienda ─────────────────────────
@router.put("/settings/{key}")
def set_setting(key: str, body: SettingIn):
    if key not in {"whatsapp_number", "store_name", "announcement", "instagram_url", "facebook_url", "admin_notify_email"}:
        raise HTTPException(404, "Ajuste desconocido")
    return get_db().table("store_settings").upsert({"key": key, "value": body.value}).execute().data[0]
