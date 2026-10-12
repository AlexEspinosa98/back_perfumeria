import hashlib
import hmac
import logging

import httpx

from app.core.config import settings

log = logging.getLogger("email")

RESEND_BATCH = "https://api.resend.com/emails/batch"
RESEND_SEND = "https://api.resend.com/emails"


def sign_unsubscribe(user_id: str) -> str:
    return hmac.new(settings.UNSUBSCRIBE_SECRET.encode(), user_id.encode(), hashlib.sha256).hexdigest()[:32]


def unsubscribe_url(user_id: str, api_base: str) -> str:
    return f"{api_base.rstrip('/')}/unsubscribe?uid={user_id}&sig={sign_unsubscribe(user_id)}"


def email_enabled() -> bool:
    return bool(settings.RESEND_API_KEY)


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.RESEND_API_KEY}", "Content-Type": "application/json"}


def send_one(to: str, subject: str, html_body: str) -> tuple[bool, str | None]:
    if not email_enabled():
        log.warning("RESEND_API_KEY vacío: correo a %s NO enviado (%s)", to, subject)
        return False, "email_disabled"
    r = httpx.post(
        RESEND_SEND,
        headers=_headers(),
        json={"from": settings.EMAIL_FROM, "to": [to], "subject": subject, "html": html_body},
        timeout=20,
    )
    return (r.is_success, None if r.is_success else r.text[:300])


def send_batch(messages: list[dict]) -> tuple[bool, str | None]:
    """messages: [{to, subject, html}] (máx. 100 por llamada, límite de Resend)."""
    if not email_enabled():
        return False, "email_disabled"
    payload = [
        {"from": settings.EMAIL_FROM, "to": [m["to"]], "subject": m["subject"], "html": m["html"]}
        for m in messages
    ]
    r = httpx.post(RESEND_BATCH, headers=_headers(), json=payload, timeout=30)
    return (r.is_success, None if r.is_success else r.text[:300])
