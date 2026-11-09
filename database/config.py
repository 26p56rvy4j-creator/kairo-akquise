def normalize_database_url(database_url: str) -> str:
    """Normalize common provider URLs to the SQLAlchemy psycopg driver."""
    value = database_url.strip()
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value[len("postgres://") :]
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value[len("postgresql://") :]
    return value
