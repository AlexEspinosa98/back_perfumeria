from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import CurrentUser, get_current_user
from app.core.supabase import get_db
from app.schemas import STATUS_LABELS, ConsentUpdate, OrderIn, ProfileUpdate
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


@router.post("/orders", status_code=201)
def create_order(body: OrderIn, user: CurrentUser = Depends(get_current_user)):
    """Crea el pedido (precios tomados del servidor) y devuelve el enlace de WhatsApp para coordinar con la tienda."""
    if not user.profile.get("data_consent_at"):
        raise HTTPException(403, "Debes autorizar el tratamiento de datos para hacer pedidos")
    db = get_db()

    qty_by_id: dict[str, int] = {}
    for it in body.items:
        qty_by_id[it.perfume_id] = qty_by_id.get(it.perfume_id, 0) + it.quantity
    perfumes = (db.table("perfumes").select("id, name, price, in_stock")
                .in_("id", list(qty_by_id)).eq("is_active", True).execute().data)
    found = {p["id"]: p for p in perfumes}
    missing = [i for i in qty_by_id if i not in found or not found[i]["in_stock"]]
    if missing:
        raise HTTPException(409, f"Productos no disponibles: {missing}")

    total = sum(int(found[i]["price"]) * q for i, q in qty_by_id.items())
    p = user.profile
    order = db.table("orders").insert({
        "user_id": user.id,
        "total": total,
        "shipping_name": body.shipping_name or p["full_name"],
        "shipping_phone": normalize_phone(body.shipping_phone or p["phone"]),
        "shipping_address": body.shipping_address or p["address"],
        "shipping_city": body.shipping_city or p["city"],
        "notes": body.notes,
    }).execute().data[0]
    try:
        db.table("order_items").insert([
            {"order_id": order["id"], "perfume_id": i, "name": found[i]["name"],
             "unit_price": found[i]["price"], "quantity": q}
            for i, q in qty_by_id.items()
        ]).execute()
        db.table("order_events").insert(
            {"order_id": order["id"], "status": "pending", "note": "Pedido recibido", "created_by": user.id}
        ).execute()
    except Exception:
        db.table("orders").delete().eq("id", order["id"]).execute()
        raise

    store = get_store()
    lines = [f"• {q} x {found[i]['name']}" for i, q in qty_by_id.items()]
    total_fmt = f"{total:,.0f}".replace(",", ".")
    text = (f"Hola, soy {order['shipping_name']}. Hice el pedido #{order['order_number']}:\n"
            + "\n".join(lines) + f"\nTotal: ${total_fmt} COP\n¿Me confirman el pago y el envío?")
    whatsapp_url = wa_link(store["whatsapp_number"], text) if store.get("whatsapp_number") else None
    return {"order": order, "whatsapp_url": whatsapp_url}
