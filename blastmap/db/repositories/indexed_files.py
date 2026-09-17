"""The `indexed_files` table: per-file content hash bookkeeping that drives
incremental re-indexing (see generation/orchestrator.py)."""
from __future__ import annotations

import sqlite3
from typing import Iterable

from ._util import now


def get_indexed_file_hashes(conn: sqlite3.Connection, service_id: int) -> dict[str, str]:
    rows = conn.execute(
        "SELECT file_path, content_hash FROM indexed_files WHERE service_id = ?", (service_id,)
    ).fetchall()
    return {row["file_path"]: row["content_hash"] for row in rows}


def set_indexed_file_hash(conn: sqlite3.Connection, service_id: int, file_path: str, content_hash: str, category: str) -> None:
    conn.execute(
        """INSERT INTO indexed_files (service_id, file_path, content_hash, category, last_indexed_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(service_id, file_path) DO UPDATE SET
             content_hash = excluded.content_hash,
             category = excluded.category,
             last_indexed_at = excluded.last_indexed_at""",
        (service_id, file_path, content_hash, category, now()),
    )


def remove_indexed_files(conn: sqlite3.Connection, service_id: int, file_paths: Iterable[str]) -> None:
    conn.executemany(
        "DELETE FROM indexed_files WHERE service_id = ? AND file_path = ?",
        [(service_id, fp) for fp in file_paths],
    )
