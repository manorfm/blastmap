"""Local semantic-embedding storage for two aggregates: one vector per service
(service_embeddings, keyed by that service's own overview text) and one vector per
change_surface_runs row (change_surface_run_embeddings, keyed by its task text).
Both are the same "vector storage for X" concern, kept in one module instead of two
near-identical files — see generation/embeddings.py for what computes the vectors
themselves; this module only persists/reads them."""
from __future__ import annotations

import json
import sqlite3

from ._util import now


def upsert_service_embedding(conn: sqlite3.Connection, service_id: int, model_name: str, vector: list[float]) -> None:
    conn.execute(
        """INSERT INTO service_embeddings (service_id, model_name, vector_json, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(service_id) DO UPDATE SET
             model_name = excluded.model_name,
             vector_json = excluded.vector_json,
             updated_at = excluded.updated_at""",
        (service_id, model_name, json.dumps(vector), now()),
    )
    conn.commit()


def get_all_service_embeddings(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT se.service_id, se.vector_json, s.name AS service_name, s.repository_id
           FROM service_embeddings se JOIN services s ON s.id = se.service_id"""
    ).fetchall()


def delete_service_embedding(conn: sqlite3.Connection, service_id: int) -> None:
    conn.execute("DELETE FROM service_embeddings WHERE service_id = ?", (service_id,))
    conn.commit()


def upsert_change_surface_run_embedding(conn: sqlite3.Connection, run_id: int, model_name: str, vector: list[float]) -> None:
    conn.execute(
        """INSERT INTO change_surface_run_embeddings (run_id, model_name, vector_json, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(run_id) DO UPDATE SET
             model_name = excluded.model_name,
             vector_json = excluded.vector_json,
             updated_at = excluded.updated_at""",
        (run_id, model_name, json.dumps(vector), now()),
    )
    conn.commit()


def get_all_change_surface_run_embeddings(
    conn: sqlite3.Connection, exclude_run_id: int | None = None
) -> list[sqlite3.Row]:
    if exclude_run_id is not None:
        return conn.execute(
            "SELECT run_id, vector_json FROM change_surface_run_embeddings WHERE run_id != ?", (exclude_run_id,)
        ).fetchall()
    return conn.execute("SELECT run_id, vector_json FROM change_surface_run_embeddings").fetchall()
