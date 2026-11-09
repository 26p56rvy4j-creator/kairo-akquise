from __future__ import annotations

import os
from sqlalchemy.engine import make_url

from database.config import normalize_database_url


def _postgres_url(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise RuntimeError(f"{key} fehlt. Kein Fallback auf einen anderen Zugang.")
    try:
        url = normalize_database_url(value)
        valid = url.startswith("postgresql+psycopg://") and bool(make_url(url).host)
    except Exception:
        valid = False
    if not valid:
        raise RuntimeError(f"{key} muss eine gültige PostgreSQL-Verbindung enthalten.") from None
    return url


def get_database_url() -> str:
    url = _postgres_url("AKQUISE_DATABASE_URL")
    if (make_url(url).username or "").split(".", 1)[0] != "akquise_app":
        raise RuntimeError("AKQUISE_DATABASE_URL muss die Rolle akquise_app verwenden, keinen Admin-Zugang.")
    return url


def get_admin_database_url() -> str:
    return _postgres_url("AKQUISE_ADMIN_DATABASE_URL")
