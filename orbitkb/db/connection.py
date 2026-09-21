from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

SCHEMA_VERSION = "3"
DEFAULT_DB_PATH = Path.home() / ".orbitkb" / "orbitkb.db"


def open_db(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    schema_sql = resources.files("orbitkb.db").joinpath("schema.sql").read_text()
    conn.executescript(schema_sql)
    _add_column_if_missing(conn, "static_message_contracts", "message_version", "TEXT")
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,)
        )
    elif row["value"] != SCHEMA_VERSION:
        conn.execute("UPDATE schema_meta SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION,))
    conn.commit()


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}  # nosec B608 - table is a module-owned constant.
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")  # nosec B608 - identifiers are module-owned constants.
