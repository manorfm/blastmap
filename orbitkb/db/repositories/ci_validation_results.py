"""Persistence for the current privacy-safe CI validation state of a change plan."""
from __future__ import annotations

import sqlite3

from ._util import now


def record_result(conn: sqlite3.Connection, plan_id: int, repository_id: int, result: dict) -> None:
    """Replace one plan command's current status without retaining process output."""
    conn.execute(
        """INSERT INTO change_plan_ci_validation_results
           (plan_id, repository_id, workflow_path, kind, command, start_line, status, duration_ms, recorded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(plan_id, repository_id, workflow_path, start_line) DO UPDATE SET
               kind = excluded.kind,
               command = excluded.command,
               status = excluded.status,
               duration_ms = excluded.duration_ms,
               recorded_at = excluded.recorded_at""",
        (
            plan_id, repository_id, result["workflow_path"], result["kind"], result["command"],
            result["start_line"], result["status"], result["duration_ms"], now(),
        ),
    )
    conn.commit()


def list_results(conn: sqlite3.Connection, plan_id: int, repository_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT workflow_path, kind, command, start_line, status, duration_ms
           FROM change_plan_ci_validation_results
           WHERE plan_id = ? AND repository_id = ?
           ORDER BY workflow_path, start_line, kind, command""",
        (plan_id, repository_id),
    ).fetchall()
