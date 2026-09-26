"""Persistence for current structured manual validation state of a change plan."""
from __future__ import annotations

import sqlite3

from ._util import now


def record_result(
    conn: sqlite3.Connection, plan_id: int, change_unit_id: str, check_index: int, status: str,
) -> None:
    """Replace one persisted check's current status without accepting free-form detail."""
    conn.execute(
        """INSERT INTO change_plan_manual_validation_results
           (plan_id, change_unit_id, check_index, status, recorded_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(plan_id, change_unit_id, check_index) DO UPDATE SET
               status = excluded.status,
               recorded_at = excluded.recorded_at""",
        (plan_id, change_unit_id, check_index, status, now()),
    )
    conn.commit()


def list_results(conn: sqlite3.Connection, plan_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT change_unit_id, check_index, status
           FROM change_plan_manual_validation_results
           WHERE plan_id = ?
           ORDER BY change_unit_id, check_index""",
        (plan_id,),
    ).fetchall()
