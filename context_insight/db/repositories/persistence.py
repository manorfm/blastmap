"""The `persistence_entities` table: what one microservice stores (tables,
documents, caches) and the field-level schema evidence backs it with."""
from __future__ import annotations

import json
import sqlite3

from ._util import now


def replace_persistence_entities(
    conn: sqlite3.Connection, service_id: int, entities: list[dict], evidence: list[dict]
) -> None:
    conn.execute("DELETE FROM persistence_entities WHERE service_id = ?", (service_id,))
    evidence_json = json.dumps(evidence)
    conn.executemany(
        """INSERT INTO persistence_entities (service_id, name, kind, schema_json, evidence_json, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (service_id, e["name"], e.get("kind"), json.dumps(e.get("schema_json", {})), evidence_json, now())
            for e in entities
        ],
    )
    conn.commit()


def list_persistence(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT name, kind, schema_json, evidence_json FROM persistence_entities WHERE service_id = ? ORDER BY name",
        (service_id,),
    ).fetchall()
