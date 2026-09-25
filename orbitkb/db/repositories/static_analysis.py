"""Versioned static-analysis input snapshots for safe AST reuse."""
from __future__ import annotations

import sqlite3

from ._util import now


def get_snapshot(conn: sqlite3.Connection, service_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT input_digest, analysis_version FROM static_analysis_snapshots WHERE service_id = ?", (service_id,),
    ).fetchone()


def replace_snapshot(conn: sqlite3.Connection, service_id: int, input_digest: str, analysis_version: str) -> None:
    conn.execute(
        """INSERT INTO static_analysis_snapshots (service_id, input_digest, analysis_version, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(service_id) DO UPDATE SET input_digest = excluded.input_digest,
             analysis_version = excluded.analysis_version, updated_at = excluded.updated_at""",
        (service_id, input_digest, analysis_version, now()),
    )


def delete_snapshot(conn: sqlite3.Connection, service_id: int) -> None:
    conn.execute("DELETE FROM static_analysis_snapshots WHERE service_id = ?", (service_id,))
