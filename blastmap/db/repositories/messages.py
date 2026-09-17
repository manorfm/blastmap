"""The `messages` table: what one microservice publishes/consumes, plus the
name-based publish<->consume linking used to connect services through a channel."""
from __future__ import annotations

import json
import sqlite3

from ._util import now


def replace_messages(conn: sqlite3.Connection, service_id: int, messages: list[dict], evidence: list[dict]) -> None:
    conn.execute("DELETE FROM messages WHERE service_id = ?", (service_id,))
    evidence_json = json.dumps(evidence)
    conn.executemany(
        """INSERT INTO messages (service_id, direction, channel, shape_json, description, evidence_json, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                service_id,
                m["direction"],
                m["channel"],
                json.dumps(m.get("shape_json", {})),
                m.get("description"),
                evidence_json,
                now(),
            )
            for m in messages
        ],
    )
    conn.commit()


def list_messages(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT direction, channel, shape_json, description, evidence_json
           FROM messages WHERE service_id = ? ORDER BY channel""",
        (service_id,),
    ).fetchall()


def list_message_links(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    """Other services connected to this one through a shared channel name.

    A 'publishes' row on this service links to every other service that 'consumes'
    the same channel, and vice versa. Purely a name match at query time — no extra
    storage, since channel is already the shared key both sides record.
    """
    return conn.execute(
        """
        SELECT m1.direction AS local_direction, m1.channel AS channel, s2.name AS other_service
        FROM messages m1
        JOIN messages m2 ON m2.channel = m1.channel AND m2.service_id != m1.service_id
                         AND m2.direction != m1.direction
        JOIN services s2 ON s2.id = m2.service_id
        WHERE m1.service_id = ?
        ORDER BY m1.channel, s2.name
        """,
        (service_id,),
    ).fetchall()
