from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.deps import CurrentUser, get_current_user, require_admin
from app.core.supabase import get_db
from app.schemas import ReviewIn, ReviewModeration, TestimonialIn, TestimonialUpdate

public_router = APIRouter(tags=["reseñas"])
me_router = APIRouter(prefix="/me", tags=["cliente"])
admin_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _author(full_name: str | None) -> str:
    """'Sofía Ramírez Pérez' -> 'Sofía R.' (no se expone el nombre completo)."""
    parts = (full_name or "").split()
    if not parts:
        return "Cliente"
    return parts[0] if len(parts) == 1 else f"{parts[0]} {parts[1][0]}."


def _public(r: dict) -> dict:
    return {
        "id": r["id"], "rating": r["rating"], "comment": r["comment"], "created_at": r["created_at"],
        "author": _author((r.get("profiles") or {}).get("full_name")),
        "perfume_id": r["perfume_id"], "perfume_name": (r.get("perfumes") or {}).get("name"),
        "verified": True,  # solo compradores pueden reseñar (ver create_review)
    }


# ───────────────────────── Público ─────────────────────────
@public_router.get("/reviews")
def list_reviews(perfume_id: str | None = None, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    q = (get_db().table("reviews").select("*, profiles(full_name), perfumes(name)")
         .eq("status", "approved"))
    if perfume_id:
        q = q.eq("perfume_id", perfume_id)
    rows = q.order("created_at", desc=True).range(offset, offset + limit - 1).execute().data
    return [_public(r) for r in rows]


@public_router.get("/reviews/summary")
def reviews_summary(perfume_id: str | None = None):
    q = get_db().table("reviews").select("rating").eq("status", "approved")
    if perfume_id:
        q = q.eq("perfume_id", perfume_id)
    ratings = [r["rating"] for r in q.execute().data]
    return {"count": len(ratings), "average": round(sum(ratings) / len(ratings), 2) if ratings else None}


@public_router.get("/testimonials")
def list_testimonials():
    return (get_db().table("testimonials").select("id, image_url, author, caption")
            .eq("is_active", True).order("sort_order").order("created_at", desc=True).execute().data)


# ───────────────────────── Cliente ─────────────────────────
@me_router.post("/reviews", status_code=201)
def create_review(body: ReviewIn, user: CurrentUser = Depends(get_current_user)):
    """Solo quien compró el perfume (pedido no cancelado) puede reseñar. Queda pendiente hasta que el admin la apruebe."""
    db = get_db()
    orders = db.table("orders").select("id").eq("user_id", user.id).neq("status", "cancelled").execute().data
    bought = bool(orders) and bool(
        db.table("order_items").select("id").eq("perfume_id", body.perfume_id)
        .in_("order_id", [o["id"] for o in orders]).limit(1).execute().data
    )
    if not bought:
        raise HTTPException(403, "Solo puedes reseñar perfumes que hayas pedido")
    if db.table("reviews").select("id").eq("user_id", user.id).eq("perfume_id", body.perfume_id).execute().data:
        raise HTTPException(409, "Ya reseñaste este perfume")
    row = db.table("reviews").insert({
        "user_id": user.id, "perfume_id": body.perfume_id, "rating": body.rating, "comment": body.comment.strip(),
    }).execute().data[0]
    return {"id": row["id"], "status": row["status"]}


@me_router.get("/reviews")
def my_reviews(user: CurrentUser = Depends(get_current_user)):
    return (get_db().table("reviews").select("id, perfume_id, rating, comment, status, created_at")
            .eq("user_id", user.id).execute().data)


# ───────────────────────── Admin ─────────────────────────
@admin_router.get("/reviews")
def admin_reviews(status: str | None = None, limit: int = Query(100, ge=1, le=200)):
    q = get_db().table("reviews").select("*, profiles(full_name, email), perfumes(name)")
    if status:
        q = q.eq("status", status)
    return q.order("created_at", desc=True).limit(limit).execute().data


@admin_router.patch("/reviews/{review_id}")
def moderate_review(review_id: str, body: ReviewModeration):
    if body.status not in ("pending", "approved", "rejected"):
        raise HTTPException(422, "Estado inválido")
    rows = get_db().table("reviews").update({"status": body.status}).eq("id", review_id).execute().data
    if not rows:
        raise HTTPException(404, "Reseña no encontrada")
    return rows[0]


@admin_router.delete("/reviews/{review_id}", status_code=204)
def delete_review(review_id: str):
    get_db().table("reviews").delete().eq("id", review_id).execute()


@admin_router.get("/testimonials")
def admin_testimonials():
    return get_db().table("testimonials").select("*").order("sort_order").order("created_at", desc=True).execute().data


@admin_router.post("/testimonials", status_code=201)
def create_testimonial(body: TestimonialIn):
    return get_db().table("testimonials").insert(body.model_dump()).execute().data[0]


@admin_router.patch("/testimonials/{testimonial_id}")
def update_testimonial(testimonial_id: str, body: TestimonialUpdate):
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(422, "Nada que actualizar")
    rows = get_db().table("testimonials").update(data).eq("id", testimonial_id).execute().data
    if not rows:
        raise HTTPException(404, "Testimonio no encontrado")
    return rows[0]


@admin_router.delete("/testimonials/{testimonial_id}", status_code=204)
def delete_testimonial(testimonial_id: str):
    get_db().table("testimonials").delete().eq("id", testimonial_id).execute()
