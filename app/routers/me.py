import html
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import CurrentUser, get_current_user
from app.core.supabase import get_db
from app.schemas import STATUS_LABELS, ConsentUpdate, OrderIn, ProfileUpdate
from app.services import email as mail
from app.services.store import get_store
from app.services.whatsapp import normalize_phone, wa_link

router = APIRouter(prefix="/me", tags=["cliente"])


@router.get("")
def get_me(user: CurrentUser = Depends(get_current_user)):
    return user.profile


@router.patch("")
def update_me(body: ProfileUpdate, user: CurrentUser = Depends(get_current_user)):
    data = body.model_dump(exclude_none=True)
    if "phone" in data:
        data["phone"] = normalize_phone(data["phone"])
    if not data:
        return user.profile
    return get_db().table("profiles").update(data).eq("id", user.id).execute().data[0]


@router.post("/consents")
def update_consents(body: ConsentUpdate, user: CurrentUser = Depends(get_current_user)):
    """Otorgar o revocar autorización de tratamiento de datos y/o de promociones (queda registrado)."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    patch: dict = {}
    logs = []
    if body.data_treatment is not None:
        patch["data_consent_at"] = now if body.data_treatment else None
        patch["data_consent_version"] = body.version if body.data_treatment else None
        logs.append({"user_id": user.id, "kind": "data_treatment", "granted": body.data_treatment, "version": body.version})
    if body.marketing is not None:
        patch["marketing_consent"] = body.marketing
        patch["unsubscribed_at"] = None if body.marketing else now
        logs.append({"user_id": user.id, "kind": "marketing", "granted": body.marketing, "version": body.version})
    if not patch:
        return user.profile
    if logs:
        db.table("consent_log").insert(logs).execute()
    return db.table("profiles").update(patch).eq("id", user.id).execute().data[0]


@router.get("/orders")
def my_orders(user: CurrentUser = Depends(get_current_user)):
    return (get_db().table("orders").select("*, order_items(*)")
            .eq("user_id", user.id).order("created_at", desc=True).execute().data)


@router.get("/orders/{order_id}")
def my_order(order_id: str, user: CurrentUser = Depends(get_current_user)):
    """Detalle con línea de tiempo de seguimiento."""
    db = get_db()
    rows = (db.table("orders").select("*, order_items(*)")
            .eq("id", order_id).eq("user_id", user.id).limit(1).execute().data)
    if not rows:
        raise HTTPException(404, "Pedido no encontrado")
    order = rows[0]
    order["events"] = (db.table("order_events").select("status, note, created_at")
                       .eq("order_id", order_id).order("created_at").execute().data)
    order["status_label"] = STATUS_LABELS.get(order["status"], order["status"])
    return order


def _stock_error(exc: Exception) -> HTTPException | None:
    """Traduce el error 'insufficient_stock:<nombre>' de la BD a una respuesta clara."""
    msg = str(getattr(exc, "message", None) or exc)
    if "insufficient_stock:" in msg:
        name = msg.split("insufficient_stock:", 1)[1].split('"')[0].strip(" '}")
        return HTTPException(409, f"No hay suficiente stock de «{name}». Reduce la cantidad o quítalo de la bolsa.")
    return None


@router.post("/orders", status_code=201)
def create_order(body: OrderIn, user: CurrentUser = Depends(get_current_user)):
    """Crea el pedido de forma atómica (valida y descuenta stock, precios del servidor).
    `contact_via`: 'reserve' = reservar y esperar contacto; 'whatsapp' = además escribir ya por WhatsApp.
    En ambos casos se devuelve `whatsapp_url` por si el cliente quiere escribir."""
    if not user.profile.get("data_consent_at"):
        raise HTTPException(403, "Debes autorizar el tratamiento de datos para hacer pedidos")
    db = get_db()

    qty_by_id: dict[str, int] = {}
    for it in body.items:
        qty_by_id[it.perfume_id] = qty_by_id.get(it.perfume_id, 0) + it.quantity
    p = user.profile
    try:
        order = db.rpc("place_order", {
            "p_user": user.id,
            "p_items": [{"perfume_id": i, "quantity": q} for i, q in qty_by_id.items()],
            "p_contact_via": body.contact_via,
            "p_name": body.shipping_name or p["full_name"],
            "p_phone": normalize_phone(body.shipping_phone or p["phone"]),
            "p_address": body.shipping_address or p["address"],
            "p_city": body.shipping_city or p["city"],
            "p_notes": body.notes,
        }).execute().data
    except Exception as exc:  # postgrest APIError
        err = _stock_error(exc)
        if err:
            raise err
        raise HTTPException(400, "No se pudo crear el pedido. Revisa los productos de tu bolsa.")

    items = db.table("order_items").select("name, quantity").eq("order_id", order["id"]).execute().data
    store = get_store()
    total_fmt = f"{int(order['total']):,}".replace(",", ".")
    lines = [f"• {i['quantity']} x {i['name']}" for i in items]
    action = "Quiero reservar" if body.contact_via == "reserve" else "Hice"
    text = (f"Hola, soy {order['shipping_name']}. {action} el pedido #{order['order_number']}:\n"
            + "\n".join(lines) + f"\nTotal: ${total_fmt} COP\n¿Me confirman el pago y el envío?")
    whatsapp_url = wa_link(store["whatsapp_number"], text) if store.get("whatsapp_number") else None

    _notify_admin_new_order(order, items, total_fmt)
    return {"order": order, "whatsapp_url": whatsapp_url}


def _notify_admin_new_order(order: dict, items: list[dict], total_fmt: str) -> None:
    """Correo opcional al admin (setting admin_notify_email). Nunca rompe la creación del pedido."""
    try:
        rows = get_db().table("store_settings").select("value").eq("key", "admin_notify_email").execute().data
        to = rows[0]["value"].strip() if rows else ""
        if not to:
            return
        lista = "".join(f"<li>{html.escape(i['name'])} × {i['quantity']}</li>" for i in items)
        via = "quiere que lo contacten" if order.get("contact_via") == "reserve" else "escribió por WhatsApp"
        mail.send_one(to, f"Nuevo pedido #{order['order_number']}",
                      f"<p>Pedido <b>#{order['order_number']}</b> de {html.escape(order['shipping_name'])} ({via}).</p>"
                      f"<ul>{lista}</ul><p>Total: ${total_fmt} COP</p>")
    except Exception:
        pass
