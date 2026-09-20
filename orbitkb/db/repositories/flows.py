"""Persistence for bounded, deterministic entrypoint flow maps."""
from __future__ import annotations

import sqlite3

from orbitkb.analysis.models import AnalysisResult
from orbitkb.db.repositories._util import now


def replace_analysis(conn: sqlite3.Connection, service_id: int, analysis: AnalysisResult) -> None:
    """Atomically replace one service's static analysis after a source scan."""
    conn.execute("DELETE FROM flow_edges WHERE service_id = ?", (service_id,))
    conn.execute("DELETE FROM entrypoints WHERE service_id = ?", (service_id,))
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
