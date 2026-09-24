from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

SCHEMA_VERSION = "18"
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
    _add_column_if_missing(conn, "service_index_locks", "process_id", "INTEGER")
    _add_column_if_missing(conn, "change_plan_runs", "decision_points_json", "TEXT NOT NULL DEFAULT '[]'")
    _add_column_if_missing(conn, "change_plan_runs", "selected_decisions_json", "TEXT NOT NULL DEFAULT '[]'")
    _add_column_if_missing(conn, "change_plan_runs", "change_units_json", "TEXT NOT NULL DEFAULT '[]'")
    _migrate_architecture_findings_if_needed(conn)
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


def _migrate_architecture_findings_if_needed(conn: sqlite3.Connection) -> None:
    """Remove the obsolete finding-kind constraint without losing historical runs.

    SQLite cannot alter a CHECK constraint in place. The table is intentionally
    rebuilt only for databases whose fixed enum would make new deterministic
    detectors require a schema migration for every finding category.
    """
    table_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'architecture_findings'"
    ).fetchone()["sql"]
    if "kind IN (" not in table_sql:
        return
    conn.executescript(
        """
        CREATE TABLE architecture_findings_replacement (
            id            INTEGER PRIMARY KEY,
            run_id        INTEGER NOT NULL REFERENCES architecture_runs(id) ON DELETE CASCADE,
            kind          TEXT NOT NULL,
            severity      TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')) DEFAULT 'info',
            services_json TEXT NOT NULL,
            detail_json   TEXT,
            reason        TEXT NOT NULL
        );
        INSERT INTO architecture_findings_replacement
            SELECT id, run_id, kind, severity, services_json, detail_json, reason
            FROM architecture_findings;
        DROP TABLE architecture_findings;
        ALTER TABLE architecture_findings_replacement RENAME TO architecture_findings;
        CREATE INDEX idx_architecture_findings_run ON architecture_findings(run_id);
        CREATE INDEX idx_architecture_findings_kind ON architecture_findings(kind);
        """
    )
