"""The `services` table: one row per indexed microservice."""
from __future__ import annotations

import sqlite3

from ._util import now


def get_service_by_name(
    conn: sqlite3.Connection, name: str, repository_id: int | None = None,
) -> sqlite3.Row | None:
    """Return one service only when its identity is unambiguous."""
    if repository_id is not None:
        return conn.execute(
            "SELECT s.*, r.name AS repository_name FROM services s "
            "LEFT JOIN repositories r ON r.id = s.repository_id "
            "WHERE s.name = ? AND s.repository_id = ?",
            (name, repository_id),
        ).fetchone()
    rows = conn.execute(
        "SELECT s.*, r.name AS repository_name FROM services s "
        "LEFT JOIN repositories r ON r.id = s.repository_id WHERE s.name = ? LIMIT 2",
        (name,),
    ).fetchall()
    return rows[0] if len(rows) == 1 else None


def get_service_by_id(conn: sqlite3.Connection, service_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM services WHERE id = ?", (service_id,)).fetchone()


def ensure_service(
    conn: sqlite3.Connection, name: str, root_path: str, stack: str, repository_id: int | None = None
) -> int:
    if repository_id is not None:
        row = get_service_by_name(conn, name, repository_id)
    else:
        candidates = list_service_candidates_by_name(conn, name)
        if len(candidates) > 1:
            raise ValueError(f"ambiguous service: {name}; specify repository")
        row = candidates[0] if candidates else None
    if row is not None:
        conn.execute(
            "UPDATE services SET root_path = ?, stack = ?, updated_at = ?, "
            "repository_id = COALESCE(?, repository_id) WHERE id = ?",
            (root_path, stack, now(), repository_id, row["id"]),
        )
        conn.commit()
        return row["id"]
    cur = conn.execute(
        "INSERT INTO services (name, root_path, stack, repository_id, updated_at) VALUES (?, ?, ?, ?, ?)",
        (name, root_path, stack, repository_id, now()),
    )
    conn.commit()
    return cur.lastrowid


def update_service_overview(conn: sqlite3.Connection, service_id: int, short_desc: str, long_desc: str) -> None:
    conn.execute(
        "UPDATE services SET short_desc = ?, long_desc = ?, updated_at = ? WHERE id = ?",
        (short_desc, long_desc, now(), service_id),
    )
    conn.commit()


def set_service_last_commit(conn: sqlite3.Connection, service_id: int, commit_sha: str | None) -> None:
    conn.execute("UPDATE services SET last_commit = ? WHERE id = ?", (commit_sha, service_id))
    conn.commit()


def list_services(conn: sqlite3.Connection, repository_id: int | None = None) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT s.id, s.name, s.stack, s.short_desc, s.repository_id, r.name AS repository_name,
               (SELECT COUNT(*) FROM apis a WHERE a.service_id = s.id) AS api_count
        FROM services s LEFT JOIN repositories r ON r.id = s.repository_id
        WHERE (? IS NULL OR s.repository_id = ?)
        ORDER BY s.name, r.name
        """,
        (repository_id, repository_id),
    ).fetchall()


def list_service_candidates_by_name(conn: sqlite3.Connection, name: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT s.*, r.name AS repository_name FROM services s "
        "LEFT JOIN repositories r ON r.id = s.repository_id WHERE s.name = ? ORDER BY r.name",
        (name,),
    ).fetchall()


def list_services_for_repository(conn: sqlite3.Connection, repository_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM services WHERE repository_id = ? ORDER BY name", (repository_id,)
    ).fetchall()
