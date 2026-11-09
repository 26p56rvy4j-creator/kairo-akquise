from __future__ import annotations

from contextlib import contextmanager
from typing import Iterable

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, CursorResult


def bind_qmark_parameters(query: str, params: Iterable | None = None) -> tuple[str, dict]:
    """Convert legacy qmark placeholders to SQLAlchemy named parameters safely."""
    values = tuple(params or ())
    output: list[str] = []
    index = 0
    quote: str | None = None
    position = 0

    while position < len(query):
        char = query[position]
        if quote:
            output.append(char)
            if char == quote:
                if position + 1 < len(query) and query[position + 1] == quote:
                    output.append(query[position + 1])
                    position += 1
                else:
                    quote = None
        elif char in ("'", '"'):
            quote = char
            output.append(char)
        elif char == "?":
            if index >= len(values):
                raise ValueError("SQL enthält mehr Platzhalter als Parameter.")
            output.append(f":p{index}")
            index += 1
        else:
            output.append(char)
        position += 1

    if index != len(values):
        raise ValueError("SQL enthält weniger Platzhalter als Parameter.")
    return "".join(output), {f"p{i}": value for i, value in enumerate(values)}


class DatabaseResult:
    def __init__(self, result: CursorResult):
        self._result = result

    @property
    def lastrowid(self):
        return self._result.lastrowid

    def fetchall(self) -> list[dict]:
        return [dict(row) for row in self._result.mappings().all()]

    def fetchone(self) -> dict | None:
        row = self._result.mappings().first()
        return dict(row) if row is not None else None


class DatabaseConnection:
    def __init__(self, connection: Connection):
        self._connection = connection
        self.backend = connection.engine.dialect.name

    def execute(self, query: str, params: Iterable | None = None) -> DatabaseResult:
        statement, bindings = bind_qmark_parameters(query, params)
        result = self._connection.execute(text(statement), bindings)
        return DatabaseResult(result)

    def insert_and_get_id(self, query: str, params: Iterable | None = None) -> int:
        if self.backend == "postgresql":
            statement = query.rstrip().rstrip(";") + " RETURNING id"
            row = self.execute(statement, params).fetchone()
            if not row:
                raise RuntimeError("PostgreSQL-Insert lieferte keine ID zurück.")
            return int(row["id"])

        result = self.execute(query, params)
        if result.lastrowid is None:
            raise RuntimeError("SQLite-Insert lieferte keine ID zurück.")
        return int(result.lastrowid)


@contextmanager
def connection_scope(engine: Engine):
    """Commit a successful unit of work and roll it back on every exception."""
    with engine.begin() as connection:
        yield DatabaseConnection(connection)
