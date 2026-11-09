from __future__ import annotations

from sqlalchemy import create_engine
import streamlit as st

from akquise.config import get_database_url
from database.session import connection_scope


@st.cache_resource
def _engine():
    return create_engine(get_database_url())


def find_user(email):
    with connection_scope(_engine()) as db:
        return db.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()


def save_user(email, name, role, active=True):
    email, name = email.strip().lower(), name.strip()
    if not email or "@" not in email or not name or role not in {"admin", "member"}:
        raise ValueError("E-Mail, Name und Rolle sind erforderlich.")
    with connection_scope(_engine()) as db:
        db.execute("INSERT INTO users(email,display_name,role,active) VALUES(?,?,?,?) "
                   "ON CONFLICT(email) DO UPDATE SET display_name=excluded.display_name, role=excluded.role, active=excluded.active",
                   (email, name, role, active))


def list_users():
    with connection_scope(_engine()) as db:
        return db.execute("SELECT id,email,display_name,role,active FROM users ORDER BY email").fetchall()
