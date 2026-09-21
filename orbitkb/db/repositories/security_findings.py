"""Persistence for deterministic, non-sensitive security findings."""
from __future__ import annotations

import sqlite3

from orbitkb.db.repositories._util import now
from orbitkb.security.findings import SecurityFinding


def replace_findings(conn: sqlite3.Connection, service_id: int, findings: list[SecurityFinding]) -> None:
    conn.execute("DELETE FROM security_findings WHERE service_id = ?", (service_id,))
    indexed_at = now()
    conn.executemany(
        """INSERT INTO security_findings (service_id, kind, severity, file_path, line, reason, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [(service_id, item.kind, item.severity, item.file_path, item.line, item.reason, indexed_at) for item in findings],
    )


def list_findings(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT kind, severity, file_path, line, reason FROM security_findings WHERE service_id = ? ORDER BY severity DESC, file_path, line",
        (service_id,),
    ).fetchall()
