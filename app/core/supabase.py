from functools import lru_cache

from supabase import Client, create_client

from app.core.config import settings


@lru_cache
def get_db() -> Client:
    """Cliente con service role: salta RLS. La autorización se hace en la API (deps.py)."""
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
