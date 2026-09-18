"""Deterministic, whole-graph architecture findings — no LLM, pure SQL + a graph
traversal over facts index/update already wrote (service_calls, persistence_entities).
Recomputed after every index/update (see orchestrator.py), so a finding is only ever as
fresh as the last indexing run; a system with unindexed services still gets findings
over the subgraph that IS indexed, the same posture find_change_surface already takes
toward partial knowledge — never silently "no smells" when the truth is "not enough
indexed to tell".
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict

from context_insight.db.repositories import architecture as architecture_repo

# Starting heuristic, not a trained threshold: flag a service once its fan-in or
# fan-out crosses this count. Low enough to catch small systems, high enough that a
# handful of legitimate dependencies doesn't trigger noise.
FAN_THRESHOLD = 4


def _internal_edges(conn: sqlite3.Connection) -> list[tuple[int, int]]:
    rows = conn.execute(
        "SELECT DISTINCT from_service_id, to_service_id FROM service_calls "
        "WHERE target_kind = 'internal' AND to_service_id IS NOT NULL"
    ).fetchall()
    return [(row["from_service_id"], row["to_service_id"]) for row in rows]


def _service_names(conn: sqlite3.Connection) -> dict[int, str]:
    return {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM services")}


def _tarjan_scc(edges: list[tuple[int, int]]) -> list[list[int]]:
    """Strongly connected components with more than one node — a genuine cycle, not
    just a node reachable from itself trivially."""
    graph: dict[int, list[int]] = defaultdict(list)
    nodes: set[int] = set()
    for a, b in edges:
        graph[a].append(b)
        nodes.add(a)
        nodes.add(b)

    index_counter = [0]
    stack: list[int] = []
    lowlink: dict[int, int] = {}
    index: dict[int, int] = {}
    on_stack: dict[int, bool] = {}
    result: list[list[int]] = []

    def strongconnect(node: int) -> None:
        index[node] = index_counter[0]
        lowlink[node] = index_counter[0]
        index_counter[0] += 1
        stack.append(node)
        on_stack[node] = True

        for neighbor in graph.get(node, []):
            if neighbor not in index:
                strongconnect(neighbor)
                lowlink[node] = min(lowlink[node], lowlink[neighbor])
            elif on_stack.get(neighbor):
                lowlink[node] = min(lowlink[node], index[neighbor])

        if lowlink[node] == index[node]:
            component: list[int] = []
            while True:
                w = stack.pop()
                on_stack[w] = False
                component.append(w)
                if w == node:
                    break
            result.append(component)

    for node in nodes:
        if node not in index:
            strongconnect(node)

    return [component for component in result if len(component) > 1]


def find_cycles(conn: sqlite3.Connection) -> list[dict]:
    edges = _internal_edges(conn)
    names = _service_names(conn)
    findings = []
    for component in _tarjan_scc(edges):
        cycle_names = sorted(names[node] for node in component if node in names)
        findings.append({
            "kind": "cycle",
            "severity": "warning",
            "services": cycle_names,
            "reason": (
                f"{' -> '.join(cycle_names)} -> {cycle_names[0]} form a circular dependency: "
                "each one reaches the next, and the chain closes back on itself."
            ),
            "detail": {},
        })
    return findings


def find_fan_imbalance(conn: sqlite3.Connection) -> list[dict]:
    names = _service_names(conn)
    findings = []
    fan_out_rows = conn.execute(
        """SELECT from_service_id, COUNT(DISTINCT to_service_id) AS n FROM service_calls
           WHERE target_kind = 'internal' AND to_service_id IS NOT NULL
           GROUP BY from_service_id HAVING n >= ?""",
        (FAN_THRESHOLD,),
    ).fetchall()
    for row in fan_out_rows:
        name = names.get(row["from_service_id"])
        if name is None:
            continue
        findings.append({
            "kind": "fan_out", "severity": "info", "services": [name],
            "reason": f"{name} calls {row['n']} other internal services directly — a broad orchestrator, or a candidate to split.",
            "detail": {"count": row["n"]},
        })
    fan_in_rows = conn.execute(
        """SELECT to_service_id, COUNT(DISTINCT from_service_id) AS n FROM service_calls
           WHERE target_kind = 'internal' AND to_service_id IS NOT NULL
           GROUP BY to_service_id HAVING n >= ?""",
        (FAN_THRESHOLD,),
    ).fetchall()
    for row in fan_in_rows:
        name = names.get(row["to_service_id"])
        if name is None:
            continue
        findings.append({
            "kind": "fan_in", "severity": "info", "services": [name],
            "reason": f"{row['n']} other internal services call {name} directly — a potential bottleneck or single point of coupling.",
            "detail": {"count": row["n"]},
        })
    return findings


def find_shared_database(conn: sqlite3.Connection) -> list[dict]:
    names = _service_names(conn)
    rows = conn.execute(
        """SELECT name, engine, GROUP_CONCAT(DISTINCT service_id) AS service_ids
           FROM persistence_entities WHERE engine != 'unknown'
           GROUP BY LOWER(name), engine HAVING COUNT(DISTINCT service_id) > 1"""
    ).fetchall()
    findings = []
    for row in rows:
        service_ids = [int(x) for x in row["service_ids"].split(",")]
        service_names = sorted(names[i] for i in service_ids if i in names)
        findings.append({
            "kind": "shared_database", "severity": "warning", "services": service_names,
            "reason": (
                f"{', '.join(service_names)} all persist an entity named '{row['name']}' on {row['engine']} — "
                "likely sharing a database, which couples their schemas."
            ),
            "detail": {"entity": row["name"], "engine": row["engine"]},
        })
    return findings


def find_duplicate_external_integrations(conn: sqlite3.Connection) -> list[dict]:
    names = _service_names(conn)
    rows = conn.execute(
        """SELECT to_service_name, GROUP_CONCAT(DISTINCT from_service_id) AS service_ids
           FROM service_calls WHERE target_kind = 'external'
           GROUP BY LOWER(to_service_name) HAVING COUNT(DISTINCT from_service_id) > 1"""
    ).fetchall()
    findings = []
    for row in rows:
        service_ids = [int(x) for x in row["service_ids"].split(",")]
        service_names = sorted(names[i] for i in service_ids if i in names)
        findings.append({
            "kind": "duplicate_external_integration", "severity": "info", "services": service_names,
            "reason": (
                f"{', '.join(service_names)} each integrate with '{row['to_service_name']}' independently — "
                "worth checking whether that's intentional or should be consolidated behind one service."
            ),
            "detail": {"vendor": row["to_service_name"]},
        })
    return findings


_DETECTORS = (find_cycles, find_fan_imbalance, find_shared_database, find_duplicate_external_integrations)


def recompute_architecture_view(conn: sqlite3.Connection) -> int:
    """Recomputes every structural finding from scratch and persists a new run — 100%
    deterministic SQL + graph traversal over already-indexed facts, no LLM call."""
    services_indexed = conn.execute("SELECT COUNT(*) AS n FROM services").fetchone()["n"]
    run_id = architecture_repo.start_run(conn, services_indexed)
    for detector in _DETECTORS:
        for finding in detector(conn):
            architecture_repo.record_finding(
                conn, run_id, finding["kind"], finding["severity"], finding["services"],
                finding["reason"], finding["detail"],
            )
    return run_id
