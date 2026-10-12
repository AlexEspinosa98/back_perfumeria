import re
from urllib.parse import quote

from app.core.config import settings


def normalize_phone(phone: str) -> str:
    """Deja solo dígitos y antepone el código de país si falta (celular colombiano = 10 dígitos)."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10:
        digits = settings.DEFAULT_COUNTRY_CODE + digits
    return digits


def wa_link(phone: str, message: str) -> str:
    """Enlace que abre WhatsApp (app o Web) del admin con el mensaje ya escrito."""
    return f"https://wa.me/{normalize_phone(phone)}?text={quote(message)}"
