"""Persistence for source-proven GitHub Actions validation commands."""
from __future__ import annotations

import sqlite3

from ._util import now


def replace_ci_commands(conn: sqlite3.Connection, repository_id: int, commands: list[dict]) -> None:
    """Replace one repository's safe, classified workflow commands atomically."""
    conn.execute("DELETE FROM ci_commands WHERE repository_id = ?", (repository_id,))
    timestamp = now()
    conn.executemany(
        """INSERT INTO ci_commands
           (repository_id, workflow_path, kind, command, file_path, start_line, end_line, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                repository_id,
                command["workflow_path"],
                command["kind"],
                command["command"],
                command["evidence"]["file"],
                command["evidence"]["start_line"],
                command["evidence"]["end_line"],
                timestamp,
            )
            for command in commands
        ],
    )
    conn.commit()


def list_ci_commands(conn: sqlite3.Connection, repository_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT workflow_path, kind, command, file_path, start_line, end_line
           FROM ci_commands WHERE repository_id = ?
           ORDER BY workflow_path, start_line, kind, command""",
        (repository_id,),
    ).fetchall()


def list_ci_commands_at_location(
    conn: sqlite3.Connection, repository_id: int, workflow_path: str, start_line: int,
) -> list[sqlite3.Row]:
    """Return commands at one exact workflow location for a safe result reference."""
    return conn.execute(
        """SELECT workflow_path, kind, command, file_path, start_line, end_line
           FROM ci_commands
           WHERE repository_id = ? AND workflow_path = ? AND start_line = ?
           ORDER BY kind, command""",
        (repository_id, workflow_path, start_line),
    ).fetchall()
