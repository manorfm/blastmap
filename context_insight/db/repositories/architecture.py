"""The `architecture_runs`/`architecture_findings` tables: deterministic, whole-graph
structural findings recomputed after every index/update. See generation/architecture.py
for the detectors themselves — this module only persists and reads their output."""
from __future__ import annotations

import json
import sqlite3

from ._util import now


def start_run(conn: sqlite3.Connection, services_indexed: int) -> int:
    cur = conn.execute(
        "INSERT INTO architecture_runs (created_at, services_indexed) VALUES (?, ?)",
        (now(), services_indexed),
    )
    conn.commit()
    return cur.lastrowid


def record_finding(
    conn: sqlite3.Connection, run_id: int, kind: str, severity: str, services: list[str], reason: str,
    detail: dict | None = None,
) -> None:
    conn.execute(
        """INSERT INTO architecture_findings (run_id, kind, severity, services_json, detail_json, reason)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (run_id, kind, severity, json.dumps(services), json.dumps(detail or {}), reason),
    )
    conn.commit()


def latest_run_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT id FROM architecture_runs ORDER BY id DESC LIMIT 1").fetchone()
    return row["id"] if row else None


def list_findings(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT kind, severity, services_json, detail_json, reason
           FROM architecture_findings WHERE run_id = ? ORDER BY severity DESC, kind""",
        (run_id,),
    ).fetchall()
