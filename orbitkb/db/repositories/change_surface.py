"""The change-surface audit trail: `change_surface_runs`, `change_surface_findings`
and `change_surface_feedback` — every find_change_surface call and the outcome
feedback agents report back (see generation/change_surface.py, which is the
task-inference logic; this module is only the storage for it)."""
from __future__ import annotations

import json
import sqlite3

from ._util import now

_ROLE_BY_RESULT_KEY = {
    "primary": "primary",
    "secondary": "secondary",
    "no_change_hint": "no_change",
    "external_integrations": "external_integration",
    "unmapped_internal_hint": "unmapped_internal",
}


def record_change_surface_run(
    conn: sqlite3.Connection,
    task_text: str,
    backend: str,
    result: dict,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO change_surface_runs (task_text, backend, created_at, input_tokens, output_tokens, cost_usd)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (task_text, backend, now(), input_tokens, output_tokens, cost_usd),
    )
    run_id = cur.lastrowid
    rows = [
        (run_id, f["service"], role, f.get("reason"), f.get("confidence"), json.dumps(f.get("evidence", [])))
        for key, role in _ROLE_BY_RESULT_KEY.items()
        for f in result.get(key, [])
    ]
    conn.executemany(
        """INSERT INTO change_surface_findings (run_id, service, role, reason, confidence, evidence_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return run_id


def get_change_surface_run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM change_surface_runs WHERE id = ?", (run_id,)).fetchone()


def list_change_surface_runs(conn: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM change_surface_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def list_change_surface_findings(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM change_surface_findings WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()


def record_change_surface_feedback(conn: sqlite3.Connection, run_id: int, service: str, outcome: str) -> None:
    conn.execute(
        "INSERT INTO change_surface_feedback (run_id, service, outcome, recorded_at) VALUES (?, ?, ?, ?)",
        (run_id, service, outcome, now()),
    )
    conn.commit()


def get_feedback_stats(conn: sqlite3.Connection, service: str) -> dict[str, int]:
    row = conn.execute(
        """SELECT
             SUM(CASE WHEN outcome = 'confirmed' THEN 1 ELSE 0 END) AS confirmed,
             SUM(CASE WHEN outcome = 'rejected' THEN 1 ELSE 0 END) AS rejected
           FROM change_surface_feedback WHERE service = ?""",
        (service,),
    ).fetchone()
    return {"confirmed": row["confirmed"] or 0, "rejected": row["rejected"] or 0}
