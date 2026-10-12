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
    is_active: bool = True
    is_featured: bool = False
    in_stock: bool = True
    category_ids: list[str] = []


class PerfumeUpdate(BaseModel):
    name: str | None = None
    brand: str | None = None
    description: str | None = None
    presentation: str | None = None
    price: int | None = Field(default=None, ge=0)
    image_url: str | None = None
    badge: str | None = None
    is_active: bool | None = None
    is_featured: bool | None = None
    in_stock: bool | None = None
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
