import re

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.config import settings
from app.core.deps import bearer

router = APIRouter(prefix="/auth", tags=["auth"])

CONSENT_VERSION = "v1"


def _gotrue(method: str, path: str, *, json: dict | None = None, token: str | None = None, params: dict | None = None):
    """Llama a Supabase Auth (GoTrue). El front nunca habla con Supabase: solo con esta API."""
    headers = {"apikey": settings.SUPABASE_ANON_KEY, "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = httpx.request(method, f"{settings.SUPABASE_URL}/auth/v1{path}", json=json, headers=headers,
                          params=params, timeout=15)
    except httpx.HTTPError:
        raise HTTPException(503, "Servicio de autenticación no disponible")
    return r


def _session(data: dict) -> dict:
    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_in": data["expires_in"],
        "expires_at": data.get("expires_at"),
    }


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    full_name: str = Field(min_length=3, max_length=120)
    phone: str
    data_consent: bool
    marketing_consent: bool = False

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, v: str) -> str:
        digits = re.sub(r"\D", "", v)
        if not 10 <= len(digits) <= 15:
            raise ValueError("Teléfono inválido")
        return digits

    @field_validator("data_consent")
    @classmethod
    def must_consent(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Debes autorizar el tratamiento de datos")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class RefreshIn(BaseModel):
    refresh_token: str


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    access_token: str          # el que llega en el enlace de recuperación
    password: str = Field(min_length=8, max_length=72)


@router.post("/register", status_code=201)
def register(body: RegisterIn):
    """Crea la cuenta. Un trigger en la BD crea el perfil con los consentimientos.
    Si el proyecto exige confirmar correo, devuelve `needs_confirmation` y el usuario confirma por email."""
    r = _gotrue("POST", "/signup", params={"redirect_to": f"{settings.FRONTEND_URL.rstrip('/')}/auth/callback"}, json={
        "email": body.email,
        "password": body.password,
        "data": {
            "full_name": body.full_name.strip(),
            "phone": body.phone,
            "data_consent": True,
            "data_consent_version": CONSENT_VERSION,
            "marketing_consent": body.marketing_consent,
        },
    })
    if r.status_code in (400, 422):
        msg = (r.json().get("msg") or r.json().get("error_description") or "").lower()
        if "already" in msg or "registered" in msg:
            raise HTTPException(409, "Ese correo ya tiene una cuenta")
        if "password" in msg:
            raise HTTPException(422, "La contraseña es muy débil o muy corta")
        raise HTTPException(400, "No se pudo crear la cuenta")
    if r.status_code == 429:
        raise HTTPException(429, "Demasiados intentos, espera un momento")
    if not r.is_success:
        raise HTTPException(502, "No se pudo crear la cuenta")
    data = r.json()
    if data.get("access_token"):
        return {"needs_confirmation": False, "session": _session(data)}
    return {"needs_confirmation": True, "session": None}


@router.post("/login")
def login(body: LoginIn):
    r = _gotrue("POST", "/token", params={"grant_type": "password"},
                json={"email": body.email, "password": body.password})
    if r.status_code in (400, 401, 422):
        msg = (r.json().get("error_description") or r.json().get("msg") or "").lower()
        if "confirm" in msg:
            raise HTTPException(403, "Debes confirmar tu correo antes de ingresar")
        raise HTTPException(401, "Correo o contraseña incorrectos")
    if r.status_code == 429:
        raise HTTPException(429, "Demasiados intentos, espera un momento")
    if not r.is_success:
        raise HTTPException(502, "No se pudo iniciar sesión")
    return _session(r.json())


@router.post("/refresh")
def refresh(body: RefreshIn):
    r = _gotrue("POST", "/token", params={"grant_type": "refresh_token"}, json={"refresh_token": body.refresh_token})
    if not r.is_success:
        raise HTTPException(401, "Sesión expirada")
    return _session(r.json())


@router.post("/logout", status_code=204)
def logout(creds: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if creds:
        _gotrue("POST", "/logout", token=creds.credentials)  # best-effort


@router.post("/forgot-password", status_code=202)
def forgot_password(body: ForgotIn):
    """Siempre responde igual, exista o no el correo (no revela qué correos tienen cuenta)."""
    _gotrue("POST", "/recover", json={"email": body.email},
            params={"redirect_to": f"{settings.FRONTEND_URL.rstrip('/')}/restablecer"})
    return {"ok": True}


@router.post("/reset-password", status_code=204)
def reset_password(body: ResetIn):
    r = _gotrue("PUT", "/user", token=body.access_token, json={"password": body.password})
    if not r.is_success:
        raise HTTPException(400, "El enlace expiró o es inválido. Solicita uno nuevo.")
