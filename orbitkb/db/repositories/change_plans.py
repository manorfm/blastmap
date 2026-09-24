"""Audit metadata for durable change-plan identifiers."""
from __future__ import annotations

import sqlite3

from ._util import now


def record_plan(
    conn: sqlite3.Connection,
    change_surface_run_id: int | None,
    status: str,
    requested_tokens: int,
) -> int:
    cur = conn.execute(
        """INSERT INTO change_plan_runs
           (change_surface_run_id, status, requested_tokens, estimated_tokens, truncated, created_at)
           VALUES (?, ?, ?, 0, 0, ?)""",
        (change_surface_run_id, status, requested_tokens, now()),
    )
    conn.commit()
    return cur.lastrowid


def update_measurements(conn: sqlite3.Connection, plan_id: int, estimated_tokens: int, truncated: bool) -> None:
    conn.execute(
        "UPDATE change_plan_runs SET estimated_tokens = ?, truncated = ? WHERE id = ?",
        (estimated_tokens, int(truncated), plan_id),
    )
    conn.commit()


def get_plan(conn: sqlite3.Connection, plan_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM change_plan_runs WHERE id = ?", (plan_id,)).fetchone()
