"""The `repositories` table: one row per indexed repository root (a monorepo or a
single-service checkout), owning the services indexed from it."""
from __future__ import annotations

import sqlite3

from ._util import now


def ensure_repository(conn: sqlite3.Connection, name: str, root_path: str) -> int:
    row = conn.execute("SELECT id FROM repositories WHERE root_path = ?", (root_path,)).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE repositories SET name = ?, updated_at = ? WHERE id = ?", (name, now(), row["id"])
        )
        conn.commit()
        return row["id"]
    cur = conn.execute(
        "INSERT INTO repositories (name, root_path, updated_at) VALUES (?, ?, ?)", (name, root_path, now())
    )
    conn.commit()
    return cur.lastrowid


def list_repositories(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT r.*, (SELECT COUNT(*) FROM services s WHERE s.repository_id = r.id) AS service_count
        FROM repositories r
        ORDER BY r.name
        """
    ).fetchall()


def get_repository_by_name(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM repositories WHERE name = ?", (name,)).fetchone()
