"""Normalized, payload-free runtime flow observations."""
from __future__ import annotations

import sqlite3

from ._util import now


def upsert_observation(conn: sqlite3.Connection, service_id: int, source: str, item: dict) -> None:
    timestamp = now()
    conn.execute(
        """INSERT INTO runtime_flow_observations
           (service_id, source, from_symbol, to_symbol, kind, observed_count, first_seen, last_seen)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(service_id, source, from_symbol, to_symbol, kind) DO UPDATE SET
             observed_count = observed_count + excluded.observed_count, last_seen = excluded.last_seen""",
        (service_id, source, item["from"], item["to"], item["kind"], item["count"], timestamp, timestamp),
    )
    conn.commit()


def list_observations(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT from_symbol, to_symbol, kind, SUM(observed_count) AS observed_count
           FROM runtime_flow_observations WHERE service_id = ?
           GROUP BY from_symbol, to_symbol, kind ORDER BY from_symbol, to_symbol, kind""", (service_id,)
    ).fetchall()
