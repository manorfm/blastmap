"""Audit metadata for durable change-plan identifiers."""
from __future__ import annotations

import json
import sqlite3

from ._util import now


def record_plan(
    conn: sqlite3.Connection,
    change_surface_run_id: int | None,
    status: str,
    requested_tokens: int,
    decision_points: list[dict],
    change_units: list[dict],
) -> int:
    cur = conn.execute(
        """INSERT INTO change_plan_runs
           (change_surface_run_id, status, requested_tokens, estimated_tokens, truncated,
            decision_points_json, change_units_json, created_at)
           VALUES (?, ?, ?, 0, 0, ?, ?, ?)""",
        (
            change_surface_run_id, status, requested_tokens, json.dumps(decision_points, sort_keys=True),
            json.dumps(change_units, sort_keys=True), now(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def update_measurements(
    conn: sqlite3.Connection, plan_id: int, estimated_tokens: int, truncated: bool, token_measurement: str,
) -> None:
    conn.execute(
        "UPDATE change_plan_runs SET estimated_tokens = ?, truncated = ?, token_measurement = ? WHERE id = ?",
        (estimated_tokens, int(truncated), token_measurement, plan_id),
    )
    conn.commit()


def get_plan(conn: sqlite3.Connection, plan_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM change_plan_runs WHERE id = ?", (plan_id,)).fetchone()


def finalize_decisions(conn: sqlite3.Connection, plan_id: int, selections: list[dict], change_units: list[dict]) -> None:
    conn.execute(
        """UPDATE change_plan_runs
           SET status = 'ready', selected_decisions_json = ?, change_units_json = ?
           WHERE id = ?""",
        (json.dumps(selections, sort_keys=True), json.dumps(change_units, sort_keys=True), plan_id),
    )
    conn.commit()
