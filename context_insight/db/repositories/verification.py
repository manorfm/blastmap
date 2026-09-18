"""The `change_surface_verifications` table: git ground-truth comparisons of a
past find_change_surface run against what a repository's commits actually changed
(see generation/verification.py for the comparison logic itself)."""
from __future__ import annotations

import json
import sqlite3

from ._util import now


def record_verification(
    conn: sqlite3.Connection,
    run_id: int,
    repository: str,
    since_commit: str,
    precision: float | None,
    recall: float | None,
    true_positives: list[str],
    false_positives: list[str],
    false_negatives: list[str],
) -> int:
    cur = conn.execute(
        """INSERT INTO change_surface_verifications
           (run_id, repository, since_commit, precision, recall,
            true_positives_json, false_positives_json, false_negatives_json, verified_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id, repository, since_commit, precision, recall,
            json.dumps(true_positives), json.dumps(false_positives), json.dumps(false_negatives), now(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def get_verification(conn: sqlite3.Connection, verification_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM change_surface_verifications WHERE id = ?", (verification_id,)
    ).fetchone()


def latest_verifications(conn: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM change_surface_verifications ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
