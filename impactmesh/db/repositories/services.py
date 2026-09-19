"""The `services` table: one row per indexed microservice."""
from __future__ import annotations

import sqlite3

from ._util import now


def get_service_by_name(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM services WHERE name = ?", (name,)).fetchone()


def get_service_by_id(conn: sqlite3.Connection, service_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM services WHERE id = ?", (service_id,)).fetchone()


def ensure_service(
    conn: sqlite3.Connection, name: str, root_path: str, stack: str, repository_id: int | None = None
) -> int:
    row = get_service_by_name(conn, name)
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


def list_services(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT s.id, s.name, s.stack, s.short_desc,
               (SELECT COUNT(*) FROM apis a WHERE a.service_id = s.id) AS api_count
        FROM services s
        ORDER BY s.name
        """
    ).fetchall()


def list_services_for_repository(conn: sqlite3.Connection, repository_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM services WHERE repository_id = ? ORDER BY name", (repository_id,)
    ).fetchall()
