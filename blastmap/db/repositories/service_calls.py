"""The `service_calls` table: outbound dependency edges from one service/API to
another service, queue or third-party integration."""
from __future__ import annotations

import json
import sqlite3

from blastmap.discovery.integration_heuristics import classify_target_kind

from ._util import now


def replace_calls_for_api(
    conn: sqlite3.Connection, from_service_id: int, api_id: int, calls: list[dict], evidence: list[dict]
) -> None:
    conn.execute("DELETE FROM service_calls WHERE from_api_id = ?", (api_id,))
    evidence_json = json.dumps(evidence)
    conn.executemany(
        """INSERT INTO service_calls
           (from_service_id, from_api_id, to_service_name, call_kind, reason, data_needed,
            purpose_kind, confidence, target_kind, evidence_json, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                from_service_id,
                api_id,
                c["to_service_name"],
                c["call_kind"],
                c.get("reason"),
                json.dumps(c.get("data_needed", [])),
                c.get("purpose_kind"),
                c.get("confidence"),
                c.get("target_kind", "unknown"),
                evidence_json,
                now(),
            )
            for c in calls
        ],
    )
    conn.commit()
    reconcile_service_call_targets(conn)


def reconcile_service_call_targets(conn: sqlite3.Connection) -> None:
    """Resolve to_service_id by exact name match, then refine target_kind:

    - Ground truth wins: any call that resolves to a real indexed service is
      'internal', full stop, overriding whatever the LLM guessed earlier.
    - For calls that stay unresolved and whose target_kind is still 'unknown' (the
      LLM couldn't tell from the code alone), fall back to the deterministic
      vendor-name / naming-convention heuristic — never LLM-driven, purely a name
      match against the vendor list and the shape of already-known service names.
    """
    conn.execute(
        """
        UPDATE service_calls
        SET to_service_id = (SELECT id FROM services WHERE services.name = service_calls.to_service_name)
        WHERE to_service_id IS NULL
           OR to_service_id != (SELECT id FROM services WHERE services.name = service_calls.to_service_name)
        """
    )
    conn.execute("UPDATE service_calls SET target_kind = 'internal' WHERE to_service_id IS NOT NULL")

    known_names = {row["name"] for row in conn.execute("SELECT name FROM services")}
    unresolved_unknown = conn.execute(
        "SELECT id, to_service_name FROM service_calls WHERE to_service_id IS NULL AND target_kind = 'unknown'"
    ).fetchall()
    for row in unresolved_unknown:
        kind = classify_target_kind(row["to_service_name"], known_names)
        if kind != "unknown":
            conn.execute("UPDATE service_calls SET target_kind = ? WHERE id = ?", (kind, row["id"]))

    conn.commit()


def list_calls_for_service(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT to_service_name, call_kind, reason, data_needed, purpose_kind, confidence,
                  target_kind, evidence_json
           FROM service_calls WHERE from_service_id = ? ORDER BY to_service_name""",
        (service_id,),
    ).fetchall()


def list_calls_for_api(conn: sqlite3.Connection, api_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT to_service_name, call_kind, reason, data_needed, purpose_kind, confidence,
                  target_kind, evidence_json
           FROM service_calls WHERE from_api_id = ? ORDER BY to_service_name""",
        (api_id,),
    ).fetchall()


def list_inbound_calls(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    """Calls made BY other services INTO this one — 'who depends on me'.

    Only resolves edges whose target has been reconciled to a real service_id
    (see reconcile_service_call_targets); a still-dangling to_service_name from
    an unindexed service can't be attributed to a from_service row here.
    """
    return conn.execute(
        """SELECT s.name AS from_service_name, sc.call_kind, sc.reason, sc.data_needed,
                  sc.purpose_kind, sc.confidence, sc.target_kind, sc.evidence_json
           FROM service_calls sc JOIN services s ON s.id = sc.from_service_id
           WHERE sc.to_service_id = ? ORDER BY s.name""",
        (service_id,),
    ).fetchall()


def list_external_integration_calls(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    """This service's outbound calls classified as third-party (target_kind='external')."""
    return conn.execute(
        """SELECT to_service_name, call_kind, reason, confidence, evidence_json
           FROM service_calls WHERE from_service_id = ? AND target_kind = 'external'
           ORDER BY to_service_name""",
        (service_id,),
    ).fetchall()


def list_unmapped_internal_calls(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    """This service's outbound calls that look internal but haven't been indexed yet."""
    return conn.execute(
        """SELECT to_service_name, call_kind, reason, confidence, evidence_json
           FROM service_calls
           WHERE from_service_id = ? AND target_kind = 'internal' AND to_service_id IS NULL
           ORDER BY to_service_name""",
        (service_id,),
    ).fetchall()
