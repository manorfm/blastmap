"""Deterministic, whole-graph architecture findings — no LLM, pure SQL + a graph
traversal over facts index/update already wrote (service_calls, persistence_entities).
Recomputed after every index/update (see orchestrator.py), so a finding is only ever as
fresh as the last indexing run; a system with unindexed services still gets findings
over the subgraph that IS indexed, the same posture find_change_surface already takes
toward partial knowledge — never silently "no smells" when the truth is "not enough
indexed to tell".
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

from orbitkb.db.repositories import architecture as architecture_repo

# Starting heuristic, not a trained threshold: flag a service once its fan-in or
# fan-out crosses this count. Low enough to catch small systems, high enough that a
# handful of legitimate dependencies doesn't trigger noise.
FAN_THRESHOLD = 4
READ_ENTRYPOINT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _internal_edges(conn: sqlite3.Connection) -> list[tuple[int, int]]:
    rows = conn.execute(
        "SELECT DISTINCT from_service_id, to_service_id FROM service_calls "
        "WHERE target_kind = 'internal' AND to_service_id IS NOT NULL"
    ).fetchall()
    return [(row["from_service_id"], row["to_service_id"]) for row in rows]


def _service_names(conn: sqlite3.Connection) -> dict[int, str]:
    """Return an unambiguous display identity for every indexed service.

    A short service name remains pleasant when unique. In a cumulative knowledge
    base, however, a repeated name must carry its repository so a whole-system
    finding cannot silently point an agent at the wrong checkout.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, r.name AS repository_name FROM services s "
        "LEFT JOIN repositories r ON r.id = s.repository_id"
    ).fetchall()
    occurrences: dict[str, int] = defaultdict(int)
    for row in rows:
        occurrences[row["name"]] += 1
    return {
        row["id"]: (
            row["name"] if occurrences[row["name"]] == 1
            else f"{row['repository_name'] or 'standalone'}/{row['name']}"
        )
        for row in rows
    }


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


def find_aggregate_ownership_overlap(conn: sqlite3.Connection) -> list[dict]:
    """Surface competing static ownership declarations across services.

    The detector intentionally relies on deterministic entity/model declarations,
    not generated descriptions or table-name guesses. Matching declarations prove
    an overlap worth reviewing; they do not prove a shared physical database or
    rule out a deliberate read model.
    """
    names = _service_names(conn)
    rows = conn.execute(
        """
        SELECT f.name, f.kind, f.owner, f.file_path, f.start_line, f.end_line,
               f.service_id
        FROM static_persistence_facts f
        ORDER BY LOWER(f.name), f.kind, f.service_id, f.owner, f.file_path, f.start_line
        """
    ).fetchall()
    grouped: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[(row["name"].casefold(), row["kind"])].append(row)

    findings: list[dict] = []
    for (_normalized_name, persistence_kind), facts in grouped.items():
        service_ids = {fact["service_id"] for fact in facts}
        if len(service_ids) < 2:
            continue
        aggregate = facts[0]["name"]
        ordered_facts = sorted(
            facts,
            key=lambda fact: (names[fact["service_id"]], fact["owner"], fact["file_path"], fact["start_line"]),
        )
        owners = sorted({(names[fact["service_id"]], fact["owner"]) for fact in facts})
        service_names = sorted(names[service_id] for service_id in service_ids)
        findings.append(
            {
                "kind": "possible_aggregate_ownership_overlap", "severity": "warning",
                "services": service_names,
                "reason": (
                    f"{', '.join(service_names)} each declare ownership of {persistence_kind} "
                    f"'{aggregate}'; validate one source of truth or an explicit read-model boundary."
                ),
                "detail": {
                    "aggregate": aggregate, "persistence_kind": persistence_kind,
                    "owners": [{"service": service, "owner": owner} for service, owner in owners],
                    "confidence": 0.65,
                    "evidence": [_edge_evidence(fact) for fact in ordered_facts],
                    "unknowns": [
                        "Static declarations cannot establish whether these services share a physical database or intentionally maintain read models.",
                    ],
                    "remediation": [
                        "Assign one write owner for the aggregate, or document the replication/read-model contract between services.",
                    ],
                },
            }
        )
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


def _edge_evidence(row: sqlite3.Row) -> dict:
    return {"file": row["file_path"], "start_line": row["start_line"], "end_line": row["end_line"]}


def _direct_entrypoint_operations(conn: sqlite3.Connection) -> list[dict]:
    """Return source-proven persistence/message operations owned by each entrypoint."""
    names = _service_names(conn)
    rows = conn.execute(
        """
        SELECT e.id AS entrypoint_id, e.kind AS entrypoint_kind, e.method, e.name,
               e.symbol, s.id AS service_id,
               fe.kind AS edge_kind, fe.to_symbol, fe.file_path, fe.start_line, fe.end_line
        FROM entrypoints e
        JOIN services s ON s.id = e.service_id
        JOIN flow_edges fe ON fe.entrypoint_id = e.id
        WHERE fe.origin = 'static' AND fe.kind IN ('writes', 'publishes')
        ORDER BY s.name, e.id, fe.id
        """
    ).fetchall()
    by_entrypoint: dict[int, dict] = {}
    for row in rows:
        entrypoint = by_entrypoint.setdefault(
            row["entrypoint_id"],
            {
                "service_id": row["service_id"], "service_name": names[row["service_id"]],
                "kind": row["entrypoint_kind"], "method": row["method"], "name": row["name"],
                "symbol": row["symbol"], "operations": [],
            },
        )
        entrypoint["operations"].append({
            "kind": row["edge_kind"], "target": row["to_symbol"], "evidence": _edge_evidence(row),
        })
    return list(by_entrypoint.values())


def _entrypoint_detail(entrypoint: dict) -> dict:
    return {
        "kind": entrypoint["kind"], "method": entrypoint["method"],
        "name": entrypoint["name"], "symbol": entrypoint["symbol"],
    }


def find_flow_hypotheses(conn: sqlite3.Connection) -> list[dict]:
    """Surface bounded flow risks as hypotheses, never as architecture verdicts.

    The evidence comes only from direct static edges owned by an entrypoint. Whether
    a GraphQL service is actually a BFF, or whether a transaction really encloses a
    publication at runtime, remains deliberately explicit in `unknowns`.
    """
    findings: list[dict] = []
    for entrypoint in _direct_entrypoint_operations(conn):
        writes = [operation for operation in entrypoint["operations"] if operation["kind"] == "writes"]
        publishes = [operation for operation in entrypoint["operations"] if operation["kind"] == "publishes"]
        entrypoint_detail = _entrypoint_detail(entrypoint)
        if entrypoint["kind"] == "graphql" and entrypoint["method"] == "MUTATION" and (writes or publishes):
            evidence = [item["evidence"] for item in [*writes, *publishes]]
            findings.append(
                {
                    "kind": "possible_bff_domain_leakage", "severity": "warning",
                    "services": [entrypoint["service_name"]],
                    "reason": (
                        "A GraphQL mutation directly writes state or publishes an event; validate whether this service "
                        "is a BFF and whether reusable domain policy belongs behind a domain service."
                    ),
                    "detail": {
                        "entrypoint": entrypoint_detail, "confidence": 0.6, "evidence": evidence,
                        "unknowns": ["The static flow cannot establish whether this GraphQL service is a BFF."],
                        "remediation": [
                            "Keep reusable domain policy behind a domain service when this service is a BFF.",
                        ],
                    },
                }
            )
        if not (writes and publishes):
            continue
        has_transaction = conn.execute(
            """SELECT 1 FROM flow_boundaries
               WHERE service_id = ? AND source = ? AND kind = 'transaction' LIMIT 1""",
            (entrypoint["service_id"], entrypoint["symbol"]),
        ).fetchone()
        if has_transaction is not None:
            continue
        findings.append(
            {
                "kind": "possible_non_atomic_publish", "severity": "warning",
                "services": [entrypoint["service_name"]],
                "reason": (
                    "One entrypoint writes state and publishes an event without a source-proven transaction boundary; "
                    "validate transactional outbox or equivalent delivery guarantees."
                ),
                "detail": {
                    "entrypoint": entrypoint_detail, "confidence": 0.5,
                    "evidence": [writes[0]["evidence"], publishes[0]["evidence"]],
                    "unknowns": ["The static flow cannot prove the runtime transaction scope or broker delivery semantics."],
                    "remediation": [
                        "Validate a transactional outbox or equivalent delivery guarantee for this write and publication.",
                    ],
                },
            }
        )
    return findings


def find_read_entrypoint_side_effects(conn: sqlite3.Connection) -> list[dict]:
    """Flag source-proven side effects behind read-only transport contracts.

    This is intentionally narrower than a generic controller-to-repository rule:
    it only observes direct static writes or publications from HTTP safe methods and
    GraphQL queries. A source fact proves the side effect; whether it is an accepted
    cache, metric or legacy exception remains explicit for human validation.
    """
    findings: list[dict] = []
    for entrypoint in _direct_entrypoint_operations(conn):
        is_safe_http = entrypoint["kind"] == "http" and entrypoint["method"] in READ_ENTRYPOINT_METHODS
        is_graphql_query = entrypoint["kind"] == "graphql" and entrypoint["method"] == "QUERY"
        if not (is_safe_http or is_graphql_query):
            continue
        operations = entrypoint["operations"]
        findings.append(
            {
                "kind": "possible_read_entrypoint_side_effect", "severity": "warning",
                "services": [entrypoint["service_name"]],
                "reason": (
                    "A read-only transport entrypoint directly writes state or publishes an event; "
                    "validate whether this observable side effect is intentional."
                ),
                "detail": {
                    "entrypoint": _entrypoint_detail(entrypoint), "confidence": 0.8,
                    "operations": [{"kind": item["kind"], "target": item["target"]} for item in operations],
                    "evidence": [item["evidence"] for item in operations],
                    "unknowns": [
                        "The static flow cannot determine whether the side effect is an approved cache, metric or legacy exception.",
                    ],
                    "remediation": [
                        "Move externally observable writes or publications behind a command entrypoint, or document the exception.",
                    ],
                },
            }
        )
    return findings


def find_message_consumers_without_recovery_policy(conn: sqlite3.Connection) -> list[dict]:
    """Flag RabbitMQ consumers without a source-proven recovery mechanism.

    A missing local declaration is not proof that the broker lacks a policy. The
    detector therefore only considers consumers whose static contract established
    RabbitMQ, and reports the missing *source proof* with a deliberately low
    confidence rather than asserting a production configuration defect.
    """
    names = _service_names(conn)
    rows = conn.execute(
        """
        SELECT e.service_id, e.name AS queue, e.symbol, e.file_path, e.start_line, e.end_line,
               c.contract_json,
               EXISTS(
                   SELECT 1 FROM flow_boundaries b
                   WHERE b.service_id = e.service_id AND b.source = e.symbol AND b.kind = 'retry'
               ) AS has_retry_boundary
        FROM entrypoints e
        JOIN entrypoint_contracts c ON c.entrypoint_id = e.id
        WHERE e.kind = 'message' AND e.method = 'CONSUME'
        ORDER BY e.service_id, e.name, e.symbol
        """
    ).fetchall()
    findings: list[dict] = []
    for row in rows:
        contract = json.loads(row["contract_json"])
        if contract.get("transport") != "rabbitmq" or contract.get("direction") != "consumes":
            continue
        dead_letter = contract.get("dead_letter_routing_key")
        retry_delay = contract.get("retry_delay_ms")
        retry_boundary = bool(row["has_retry_boundary"])
        if dead_letter is not None or retry_delay is not None or retry_boundary:
            continue
        findings.append(
            {
                "kind": "possible_message_consumer_without_recovery_policy", "severity": "warning",
                "services": [names[row["service_id"]]],
                "reason": (
                    "A RabbitMQ consumer has no source-proven retry boundary, retry delay or dead-letter route; "
                    "validate its recovery policy."
                ),
                "detail": {
                    "consumer": {"queue": row["queue"], "symbol": row["symbol"]},
                    "source_proven": {
                        "dead_letter_routing_key": dead_letter,
                        "retry_delay_ms": retry_delay,
                        "retry_boundary": retry_boundary,
                    },
                    "confidence": 0.45,
                    "evidence": [{
                        "file": row["file_path"], "start_line": row["start_line"], "end_line": row["end_line"],
                    }],
                    "unknowns": [
                        "Broker topology or retry policy may be declared outside the indexed source/configuration.",
                    ],
                    "remediation": [
                        "Confirm a retry and dead-letter policy in broker configuration, then declare it near the consumer when practical.",
                    ],
                },
            }
        )
    return findings


_DETECTORS = (
    find_cycles, find_fan_imbalance, find_shared_database, find_aggregate_ownership_overlap,
    find_duplicate_external_integrations,
    find_flow_hypotheses, find_read_entrypoint_side_effects,
    find_message_consumers_without_recovery_policy,
)


def _findings_by_identity(conn: sqlite3.Connection, run_id: int) -> dict[tuple[str, tuple[str, ...], str], dict]:
    """Give findings a stable identity across recomputed runs.

    Structural findings are scoped by kind and services. Flow hypotheses additionally
    need their entrypoint: one service can legitimately expose several independently
    risky flows, and collapsing them would hide a newly detected one in `trend`.
    """
    identified: dict[tuple[str, tuple[str, ...], str], dict] = {}
    for f in architecture_repo.list_findings(conn, run_id):
        services = tuple(sorted(json.loads(f["services_json"])))
        detail = json.loads(f["detail_json"] or "{}")
        entrypoint = json.dumps(detail.get("entrypoint"), sort_keys=True, separators=(",", ":"))
        identified[(f["kind"], services, entrypoint)] = {
            "kind": f["kind"], "services": list(services), "detail": detail,
        }
    return identified


def diff_architecture_runs(conn: sqlite3.Connection, previous_run_id: int, current_run_id: int) -> dict:
    """Pure SQL/in-memory diff between two already-computed architecture runs — no
    LLM, no re-detection: new_findings/resolved_findings by (kind, services,
    entrypoint when present) identity, plus a numeric count_deltas entry for any fan_in/fan_out finding that
    persisted across both runs but whose count changed. Recomputed for free from
    data find_architecture_smells already has to read anyway.
    """
    previous = _findings_by_identity(conn, previous_run_id)
    current = _findings_by_identity(conn, current_run_id)

    new_findings = [finding for key, finding in current.items() if key not in previous]
    resolved_findings = [finding for key, finding in previous.items() if key not in current]

    count_deltas = []
    for key, current_finding in current.items():
        if key not in previous:
            continue
        previous_count = previous[key]["detail"].get("count")
        current_count = current_finding["detail"].get("count")
        if previous_count is not None and current_count is not None and previous_count != current_count:
            count_deltas.append({
                "kind": current_finding["kind"],
                "services": current_finding["services"],
                "previous_count": previous_count,
                "current_count": current_count,
            })

    return {"new_findings": new_findings, "resolved_findings": resolved_findings, "count_deltas": count_deltas}


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
