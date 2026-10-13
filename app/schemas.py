from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

ORDER_STATUSES = ["pending", "confirmed", "paid", "preparing", "shipped", "delivered", "cancelled"]
STATUS_LABELS = {
    "pending": "Pendiente de confirmación",
    "confirmed": "Confirmado",
    "paid": "Pago recibido",
    "preparing": "En preparación",
    "shipped": "Enviado",
    "delivered": "Entregado",
    "cancelled": "Cancelado",
}


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    slug: str | None = None
    sort_order: int = 0


class PerfumeIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    brand: str = ""
    description: str = ""
    presentation: str = ""
    price: int = Field(ge=0)
    image_url: str | None = None
    badge: str | None = None
    images: list[str] = Field(default_factory=list, max_length=3)   # hasta 3 fotos; la primera es la principal
    is_active: bool = True
    is_featured: bool = False
    stock: int = Field(default=0, ge=0, le=100000)      # "Disponible" se calcula solo: stock > 0
    cost: int = Field(default=0, ge=0)                  # precio base (lo que te cuesta)
    category_ids: list[str] = []


class PerfumeUpdate(BaseModel):
    name: str | None = None
    brand: str | None = None
    description: str | None = None
    presentation: str | None = None
    price: int | None = Field(default=None, ge=0)
    image_url: str | None = None
    badge: str | None = None
    images: list[str] | None = Field(default=None, max_length=3)
    is_active: bool | None = None
    is_featured: bool | None = None
    stock: int | None = Field(default=None, ge=0, le=100000)
    cost: int | None = Field(default=None, ge=0)
    category_ids: list[str] | None = None


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=20)
    address: str | None = Field(default=None, max_length=250)
    city: str | None = Field(default=None, max_length=100)


class ConsentUpdate(BaseModel):
    data_treatment: bool | None = None   # revocar = solicitar supresión; ver /me/consents
    marketing: bool | None = None
    version: str = "v1"


class OrderItemIn(BaseModel):
    perfume_id: str
    quantity: int = Field(ge=1, le=50)


class OrderIn(BaseModel):
    items: list[OrderItemIn] = Field(min_length=1)
    contact_via: Literal["reserve", "whatsapp"] = "whatsapp"   # reservar y esperar contacto / escribir por WhatsApp
    shipping_name: str = ""
    shipping_phone: str = ""
    shipping_address: str = ""
    shipping_city: str = ""
    notes: str = ""


class OrderStatusUpdate(BaseModel):
    status: str
    note: str = ""
    tracking_code: str | None = None
    carrier: str | None = None
    notify_email: bool = True


class EmailTemplateIn(BaseModel):
    name: str
    subject: str
    html: str


class WhatsappTemplateIn(BaseModel):
    name: str
    body: str


class CampaignIn(BaseModel):
    name: str
    template_id: str
    only_user_ids: list[str] | None = None   # None = todos los que aceptaron promociones


class SendIndividualIn(BaseModel):
    template_id: str
    user_id: str


class SettingIn(BaseModel):
    value: str


class ReviewIn(BaseModel):
    perfume_id: str
    rating: int = Field(ge=1, le=5)
    comment: str = Field(min_length=10, max_length=1000)


class ReviewModeration(BaseModel):
    status: str  # pending | approved | rejected


class TestimonialIn(BaseModel):
    image_url: str = Field(min_length=5)
    author: str = Field(default="", max_length=80)
    caption: str = Field(default="", max_length=500)
    is_active: bool = True
    sort_order: int = 0


class TestimonialUpdate(BaseModel):
    image_url: str | None = None
    author: str | None = Field(default=None, max_length=80)
    caption: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None
    sort_order: int | None = None


# ───────────── Contabilidad ─────────────
class SaleItemIn(BaseModel):
    perfume_id: str | None = None          # del catálogo (toma nombre y costo) o...
    name: str | None = Field(default=None, max_length=160)   # ...escrito a mano
    quantity: int = Field(ge=1, le=1000)
    unit_price: int = Field(ge=0)           # en cuánto se vendió cada unidad
    unit_cost: int | None = Field(default=None, ge=0)   # opcional: si no se envía, usa el costo del catálogo


class SaleIn(BaseModel):
    sold_at: datetime | None = None
    channel: Literal["web", "whatsapp", "presencial", "otro"] = "whatsapp"
    customer_name: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=500)
    items: list[SaleItemIn] = Field(min_length=1)
    decrement_stock: bool = True


class SaleUpdate(BaseModel):
    sold_at: datetime | None = None
    channel: Literal["web", "whatsapp", "presencial", "otro"] | None = None
    customer_name: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=500)


class ExpenseIn(BaseModel):
    spent_at: date | None = None
    amount: int = Field(gt=0)
    reason: str = Field(min_length=2, max_length=200)
    category: str = Field(default="otros", max_length=40)
    note: str = Field(default="", max_length=500)


class ExpenseUpdate(BaseModel):
    spent_at: date | None = None
    amount: int | None = Field(default=None, gt=0)
    reason: str | None = Field(default=None, min_length=2, max_length=200)
    category: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=500)


class BannerIn(BaseModel):
    image_url: str = Field(min_length=5, max_length=600)
    mobile_image_url: str | None = Field(default=None, max_length=600)
    title: str = Field(default="", max_length=120)
    subtitle: str = Field(default="", max_length=300)
    button_label: str = Field(default="", max_length=40)
    link_url: str = Field(default="", max_length=500)    # "/colecciones?category=hombres" o "https://..."
    is_active: bool = True
    sort_order: int = 0


class BannerUpdate(BaseModel):
    image_url: str | None = Field(default=None, min_length=5, max_length=600)
    mobile_image_url: str | None = Field(default=None, max_length=600)
    title: str | None = Field(default=None, max_length=120)
    subtitle: str | None = Field(default=None, max_length=300)
    button_label: str | None = Field(default=None, max_length=40)
    link_url: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None
    sort_order: int | None = None
