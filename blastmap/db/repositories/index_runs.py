"""The `index_runs` table: one row per `index`/`update` invocation for one
service, used by `blastmap status` to show indexing history."""
from __future__ import annotations

import sqlite3

from ._util import now


def start_index_run(conn: sqlite3.Connection, service_id: int | None, backend: str) -> int:
    cur = conn.execute(
        "INSERT INTO index_runs (service_id, started_at, backend, status) VALUES (?, ?, ?, 'partial')",
        (service_id, now(), backend),
    )
    conn.commit()
    return cur.lastrowid


def finish_index_run(
    conn: sqlite3.Connection, run_id: int, status: str, files_changed: int, llm_calls: int, notes: str | None
) -> None:
    conn.execute(
        """UPDATE index_runs SET finished_at = ?, status = ?, files_changed = ?, llm_calls = ?, notes = ?
           WHERE id = ?""",
        (now(), status, files_changed, llm_calls, notes, run_id),
    )
    conn.commit()


def recent_index_runs(conn: sqlite3.Connection, service_id: int | None = None, limit: int = 10) -> list[sqlite3.Row]:
    if service_id is not None:
        return conn.execute(
            "SELECT * FROM index_runs WHERE service_id = ? ORDER BY id DESC LIMIT ?", (service_id, limit)
        ).fetchall()
    return conn.execute("SELECT * FROM index_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
