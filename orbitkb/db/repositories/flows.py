"""Persistence for bounded, deterministic entrypoint flow maps."""
from __future__ import annotations

import json
import sqlite3

from orbitkb.analysis.models import AnalysisResult
from orbitkb.db.repositories._util import now


def replace_analysis(conn: sqlite3.Connection, service_id: int, analysis: AnalysisResult) -> None:
    """Atomically replace one service's static analysis after a source scan."""
    conn.execute("DELETE FROM flow_edges WHERE service_id = ?", (service_id,))
    conn.execute("DELETE FROM flow_boundaries WHERE service_id = ?", (service_id,))
    conn.execute("DELETE FROM entrypoints WHERE service_id = ?", (service_id,))
    conn.execute("DELETE FROM static_message_contracts WHERE service_id = ?", (service_id,))
    conn.execute("DELETE FROM static_persistence_facts WHERE service_id = ?", (service_id,))
    conn.execute("DELETE FROM static_cloud_facts WHERE service_id = ?", (service_id,))
    indexed_at = now()
    entrypoint_ids: dict[str, int] = {}
    for entry in analysis.entrypoints:
        cursor = conn.execute(
            """INSERT INTO entrypoints
               (service_id, kind, method, name, symbol, file_path, start_line, end_line, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                service_id, entry.kind, entry.method, entry.name, entry.symbol,
                entry.evidence.file_path, entry.evidence.start_line, entry.evidence.end_line, indexed_at,
            ),
        )
        entrypoint_ids[entry.symbol] = cursor.lastrowid
    for symbol, contract in analysis.contracts.items():
        entrypoint_id = entrypoint_ids.get(symbol)
        if entrypoint_id is not None:
            conn.execute(
                "INSERT INTO entrypoint_contracts (entrypoint_id, contract_json) VALUES (?, ?)",
                (entrypoint_id, json.dumps(contract)),
            )
    for edge in analysis.edges:
        conn.execute(
            """INSERT INTO flow_edges
               (service_id, entrypoint_id, from_symbol, to_symbol, kind, confidence, origin, file_path, start_line, end_line, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                service_id, entrypoint_ids.get(edge.source), edge.source, edge.target,
                edge.kind, edge.confidence, edge.origin, edge.evidence.file_path,
                edge.evidence.start_line, edge.evidence.end_line, indexed_at,
            ),
        )
    for contract in analysis.message_contracts:
        conn.execute(
            """INSERT INTO static_message_contracts
               (service_id, direction, channel, routing_key, payload_type, message_version, file_path, start_line, end_line, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (service_id, contract.direction, contract.channel, contract.routing_key, contract.payload_type, contract.message_version,
             contract.evidence.file_path, contract.evidence.start_line, contract.evidence.end_line, indexed_at),
        )
    for boundary in analysis.boundaries:
        conn.execute(
            """INSERT INTO flow_boundaries (service_id, source, kind, file_path, start_line, end_line, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (service_id, boundary.source, boundary.kind, boundary.evidence.file_path,
             boundary.evidence.start_line, boundary.evidence.end_line, indexed_at),
        )
    for fact in analysis.persistence_facts:
        conn.execute(
            """INSERT INTO static_persistence_facts
               (service_id, name, kind, owner, file_path, start_line, end_line, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (service_id, fact.name, fact.kind, fact.owner, fact.evidence.file_path,
             fact.evidence.start_line, fact.evidence.end_line, indexed_at),
        )
    for fact in analysis.cloud_facts:
        conn.execute(
            """INSERT INTO static_cloud_facts
               (service_id, provider, resource_type, service_name, operation, operation_kind,
                sdk, target_name, file_path, start_line, end_line, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (service_id, fact.provider, fact.resource_type, fact.service_name, fact.operation,
             fact.operation_kind, fact.sdk, fact.target_name, fact.evidence.file_path,
             fact.evidence.start_line, fact.evidence.end_line, indexed_at),
        )


def list_entrypoints(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM entrypoints WHERE service_id = ? ORDER BY kind, method, name", (service_id,)
    ).fetchall()


def get_entrypoint(conn: sqlite3.Connection, service_id: int, kind: str, method: str, name: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM entrypoints WHERE service_id = ? AND kind = ? AND method = ? AND name = ?",
        (service_id, kind, method.upper(), name),
    ).fetchone()


def list_entrypoint_edges(conn: sqlite3.Connection, entrypoint_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM flow_edges WHERE entrypoint_id = ? ORDER BY id", (entrypoint_id,)
    ).fetchall()


def list_flow_edges(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    """All static/provider/runtime-indexed flow edges for one service."""
    return conn.execute(
        "SELECT from_symbol, to_symbol, kind FROM flow_edges WHERE service_id = ?", (service_id,)
    ).fetchall()


def get_entrypoint_contract(conn: sqlite3.Connection, entrypoint_id: int) -> dict | None:
    row = conn.execute(
        "SELECT contract_json FROM entrypoint_contracts WHERE entrypoint_id = ?", (entrypoint_id,)
    ).fetchone()
    return json.loads(row["contract_json"]) if row else None


def list_static_message_contracts(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT direction, channel, routing_key, payload_type, message_version, file_path, start_line, end_line
           FROM static_message_contracts WHERE service_id = ? ORDER BY channel, routing_key""",
        (service_id,),
    ).fetchall()


def list_flow_boundaries(conn: sqlite3.Connection, service_id: int, symbols: set[str]) -> list[sqlite3.Row]:
    if not symbols:
        return []
    placeholders = ", ".join("?" for _ in symbols)
    return conn.execute(
        f"SELECT source, kind, file_path, start_line, end_line FROM flow_boundaries WHERE service_id = ? AND source IN ({placeholders}) ORDER BY id",  # nosec B608 - placeholders are generated from set cardinality; values are bound.
        (service_id, *sorted(symbols)),
    ).fetchall()


def list_static_persistence_facts(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT name, kind, owner, file_path, start_line, end_line FROM static_persistence_facts WHERE service_id = ? ORDER BY name",
        (service_id,),
    ).fetchall()


def list_static_cloud_facts(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT provider, resource_type, service_name, operation, operation_kind, sdk, target_name,
                  file_path, start_line, end_line
           FROM static_cloud_facts WHERE service_id = ? ORDER BY service_name, operation""",
        (service_id,),
    ).fetchall()


def list_all_static_cloud_facts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every service's cloud fact in the whole system — the topology diagram's
    cloud edge set (export/mermaid.py), same posture as service_calls'
    list_external_edges."""
    return conn.execute(
        """SELECT DISTINCT s.name AS from_name, scf.provider, scf.resource_type,
                  scf.service_name, scf.target_name
           FROM static_cloud_facts scf
           JOIN services s ON s.id = scf.service_id
           ORDER BY from_name, scf.service_name"""
    ).fetchall()


def list_reachable_edges(conn: sqlite3.Connection, service_id: int, symbol: str, max_edges: int = 100) -> list[sqlite3.Row]:
    """Breadth-first bounded traversal from one entrypoint's resolved symbol."""
    pending = [symbol]
    seen_symbols: set[str] = set()
    seen_edges: set[int] = set()
    result: list[sqlite3.Row] = []
    while pending and len(result) < max_edges:
        source = pending.pop(0)
        if source in seen_symbols:
            continue
        seen_symbols.add(source)
        rows = conn.execute(
            "SELECT * FROM flow_edges WHERE service_id = ? AND from_symbol = ? ORDER BY id",
            (service_id, source),
        ).fetchall()
        for edge in rows:
            if edge["id"] in seen_edges:
                continue
            seen_edges.add(edge["id"])
            result.append(edge)
            if edge["to_symbol"] not in seen_symbols:
                pending.append(edge["to_symbol"])
            if len(result) >= max_edges:
                break
    return result
