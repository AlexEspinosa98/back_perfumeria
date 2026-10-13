from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.deps import CurrentUser, require_admin
from app.core.supabase import get_db
from app.schemas import ExpenseIn, ExpenseUpdate, SaleIn, SaleUpdate

router = APIRouter(prefix="/admin", tags=["contabilidad"], dependencies=[Depends(require_admin)])

BOGOTA = timezone(timedelta(hours=-5))   # Colombia no usa horario de verano
MAX_RANGE_DAYS = 800


def _range(d_from: date, d_to: date) -> None:
    if d_from > d_to:
        raise HTTPException(422, "La fecha inicial no puede ser posterior a la final")
    if (d_to - d_from).days > MAX_RANGE_DAYS:
        raise HTTPException(422, "El rango es demasiado grande")


def _bounds(d_from: date, d_to: date) -> tuple[str, str]:
    """[desde 00:00, hasta+1 00:00) en hora de Colombia, como ISO con offset."""
    start = datetime(d_from.year, d_from.month, d_from.day, tzinfo=BOGOTA)
    end = datetime(d_to.year, d_to.month, d_to.day, tzinfo=BOGOTA) + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _db_error(exc: Exception) -> HTTPException:
    msg = str(getattr(exc, "message", None) or exc)
    if "insufficient_stock:" in msg:
        name = msg.split("insufficient_stock:", 1)[1].split('"')[0].strip(" '}")
        return HTTPException(409, f"No hay stock suficiente de «{name}». Ajusta el stock o desmarca «Descontar del stock».")
    if "item_name_required" in msg:
        return HTTPException(422, "Cada producto escrito a mano necesita un nombre")
    if "perfume_not_found" in msg:
        return HTTPException(404, "Uno de los perfumes ya no existe")
    if "sale_not_found" in msg:
        return HTTPException(404, "Venta no encontrada")
    return HTTPException(400, "No se pudo completar la operación")


def _with_totals(sale: dict) -> dict:
    items = sale.get("sale_items") or []
    revenue = sum(i["quantity"] * float(i["unit_price"]) for i in items)
    cost = sum(i["quantity"] * float(i["unit_cost"]) for i in items)
    return {**sale, "revenue": revenue, "cost": cost, "profit": revenue - cost}


# ───────────────────────── Resumen / balance ─────────────────────────
@router.get("/accounting/summary")
def summary(date_from: date = Query(alias="from"), date_to: date = Query(alias="to")):
    """Ingresos, costo de ventas, gastos, balance, top productos, canales e inventario del periodo."""
    _range(date_from, date_to)
    data = get_db().rpc("accounting_summary", {"p_from": date_from.isoformat(), "p_to": date_to.isoformat()}).execute().data
    revenue, cogs, expenses = float(data["revenue"]), float(data["cogs"]), float(data["expenses"])
    data["gross_profit"] = revenue - cogs
    data["gross_margin"] = round((revenue - cogs) / revenue * 100, 1) if revenue else None
    data["net_profit"] = revenue - cogs - expenses          # balance del periodo
    data["avg_ticket"] = round(revenue / data["sales_count"]) if data["sales_count"] else 0
    return data


# ───────────────────────── Ventas ─────────────────────────
@router.get("/sales")
def list_sales(date_from: date = Query(alias="from"), date_to: date = Query(alias="to"),
               limit: int = Query(200, ge=1, le=500)):
    _range(date_from, date_to)
    start, end = _bounds(date_from, date_to)
    rows = (get_db().table("sales").select("*, sale_items(*)")
            .gte("sold_at", start).lt("sold_at", end).order("sold_at", desc=True).limit(limit).execute().data)
    return [_with_totals(r) for r in rows]


@router.post("/sales", status_code=201)
def create_sale(body: SaleIn, admin: CurrentUser = Depends(require_admin)):
    """Registra una venta con uno o varios productos (del catálogo o escritos a mano)."""
    for it in body.items:
        if not it.perfume_id and not (it.name and it.name.strip()):
            raise HTTPException(422, "Cada producto necesita un perfume del catálogo o un nombre")
    db = get_db()
    try:
        sale_id = db.rpc("record_sale", {
            "p_sold_at": body.sold_at.isoformat() if body.sold_at else None,
            "p_channel": body.channel,
            "p_customer": body.customer_name.strip(),
            "p_note": body.note.strip(),
            "p_items": [i.model_dump(exclude_none=True) for i in body.items],
            "p_decrement": body.decrement_stock,
            "p_by": admin.id,
        }).execute().data
    except Exception as exc:
        raise _db_error(exc)
    row = db.table("sales").select("*, sale_items(*)").eq("id", sale_id).limit(1).execute().data[0]
    return _with_totals(row)


@router.patch("/sales/{sale_id}")
def update_sale(sale_id: str, body: SaleUpdate):
    """Edita una venta ya registrada: fecha, canal, cliente, nota y/o sus líneas (cantidad, precio, costo, productos).
    Si la venta descontó stock, el stock se ajusta solo; si viene de un pedido web, el stock no se toca."""
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(422, "Nada que actualizar")
    if body.items is not None:
        for it in body.items:
            if not it.perfume_id and not (it.name and it.name.strip()):
                raise HTTPException(422, "Cada producto necesita un perfume del catálogo o un nombre")
    db = get_db()
    try:
        db.rpc("update_sale", {
            "p_sale": sale_id,
            "p_sold_at": body.sold_at.isoformat() if body.sold_at else None,
            "p_channel": body.channel,
            "p_customer": body.customer_name.strip() if body.customer_name is not None else None,
            "p_note": body.note.strip() if body.note is not None else None,
            "p_items": [i.model_dump(exclude_none=True) for i in body.items] if body.items is not None else None,
        }).execute()
    except Exception as exc:
        raise _db_error(exc)
    row = db.table("sales").select("*, sale_items(*)").eq("id", sale_id).limit(1).execute().data
    if not row:
        raise HTTPException(404, "Venta no encontrada")
    return _with_totals(row[0])


@router.delete("/sales/{sale_id}", status_code=204)
def delete_sale(sale_id: str, restore_stock: bool = True):
    """Elimina la venta. Si fue manual y descontó stock, `restore_stock` lo devuelve."""
    try:
        get_db().rpc("delete_sale", {"p_sale": sale_id, "p_restore": restore_stock}).execute()
    except Exception as exc:
        raise _db_error(exc)


# ───────────────────────── Gastos operativos ─────────────────────────
@router.get("/expenses")
def list_expenses(date_from: date = Query(alias="from"), date_to: date = Query(alias="to"),
                  limit: int = Query(300, ge=1, le=500)):
    _range(date_from, date_to)
    return (get_db().table("expenses").select("*")
            .gte("spent_at", date_from.isoformat()).lte("spent_at", date_to.isoformat())
            .order("spent_at", desc=True).order("created_at", desc=True).limit(limit).execute().data)


@router.post("/expenses", status_code=201)
def create_expense(body: ExpenseIn, admin: CurrentUser = Depends(require_admin)):
    data = body.model_dump()
    data["spent_at"] = (body.spent_at or datetime.now(BOGOTA).date()).isoformat()
    data["reason"] = body.reason.strip()
    data["category"] = body.category.strip().lower() or "otros"
    data["created_by"] = admin.id
    return get_db().table("expenses").insert(data).execute().data[0]


@router.patch("/expenses/{expense_id}")
def update_expense(expense_id: str, body: ExpenseUpdate):
    data = body.model_dump(exclude_unset=True)
    if "spent_at" in data and data["spent_at"] is not None:
        data["spent_at"] = data["spent_at"].isoformat()
    if "category" in data and data["category"] is not None:
        data["category"] = data["category"].strip().lower() or "otros"
    if not data:
        raise HTTPException(422, "Nada que actualizar")
    rows = get_db().table("expenses").update(data).eq("id", expense_id).execute().data
    if not rows:
        raise HTTPException(404, "Gasto no encontrado")
    return rows[0]


@router.delete("/expenses/{expense_id}", status_code=204)
def delete_expense(expense_id: str):
    get_db().table("expenses").delete().eq("id", expense_id).execute()
