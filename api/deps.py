# api/deps.py
"""
FastAPI dependency injection.
"""
from fastapi import HTTPException
from loguru import logger

_db_client = None


def _get_db_client():
    """Singleton Supabase client — retries if previous attempt failed."""
    global _db_client
    if _db_client is not None:
        return _db_client
    try:
        from database.supabase_client import SupabaseClient
        _db_client = SupabaseClient()
        return _db_client
    except Exception as e:
        logger.error(f"Failed to connect to Supabase: {e}")
        return None


def get_db():
    """
    Dependency that yields a Supabase client.
    Raises 503 if Supabase is not configured.
    """
    db = _get_db_client()
    if db is None:
        raise HTTPException(
            status_code=503,
            detail="Database not available. Check SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env",
        )
    return db
