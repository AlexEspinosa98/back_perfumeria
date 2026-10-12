from dataclasses import dataclass

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.core.supabase import get_db

bearer = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    id: str
    email: str
    role: str
    profile: dict

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> CurrentUser:
    """Valida el access token de Supabase Auth consultando /auth/v1/user.

    Funciona con cualquier algoritmo de firma del proyecto (HS256 o claves asimétricas).
    """
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Falta el token")
    try:
        resp = httpx.get(
            f"{settings.SUPABASE_URL}/auth/v1/user",
            headers={"Authorization": f"Bearer {creds.credentials}", "apikey": settings.SUPABASE_ANON_KEY},
            timeout=10,
        )
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No se pudo validar la sesión")
    if resp.status_code != 200:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido o expirado")
    uid = resp.json()["id"]

    rows = get_db().table("profiles").select("*").eq("id", uid).limit(1).execute().data
    if not rows:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Perfil no encontrado")
    profile = rows[0]
    return CurrentUser(id=uid, email=profile["email"], role=profile["role"], profile=profile)


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Solo administradores")
    return user
