"""Read helpers backing the MCP tools. Thin JSON-shaping wrappers over the
db.repositories.* modules (and, for find_change_surface, over generation.change_surface)."""
from __future__ import annotations

import json
import sqlite3
from collections import deque

from orbitkb.analysis.smells import find_entrypoint_smells
from orbitkb.db.repositories import apis as apis_repo
from orbitkb.db.repositories import architecture as architecture_repo
from orbitkb.db.repositories import change_surface as change_surface_repo
from orbitkb.db.repositories import components as components_repo
from orbitkb.db.repositories import flows as flows_repo
from orbitkb.db.repositories import messages as messages_repo
from orbitkb.db.repositories import persistence as persistence_repo
from orbitkb.db.repositories import repositories as repositories_repo
from orbitkb.db.repositories import search as search_repo
from orbitkb.db.repositories import service_calls as service_calls_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.generation import change_surface
from orbitkb.generation.architecture import diff_architecture_runs
from orbitkb.generation.backend_base import LLMBackend
from orbitkb.generation.freshness import compute_freshness
from orbitkb.generation.provenance import infer_provenance
from orbitkb.generation.verification import (
    verify_change_surface as _verify_change_surface,
)

# Progressive-disclosure budget for list-shaped MCP responses (describe_service's own
# lists, list_apis, describe_persistence, describe_messages): a real service can have
# far more endpoints/entities/messages than a benchmark fixture, so every such list is
# capped by default instead of returned whole — see README's context-efficiency notes.
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 500


def _validate_pagination(limit: int, offset: int) -> str | None:
    if limit < 1:
        return f"limit must be >= 1 (got {limit})"
    if offset < 0:
        return f"offset must be >= 0 (got {offset})"
    return None


def _paginate(items: list, limit: int, offset: int) -> tuple[list, dict]:
    limit = min(limit, MAX_LIST_LIMIT)
    total = len(items)
    page = items[offset : offset + limit]
    return page, {"total": total, "truncated": offset + len(page) < total}


def _fmt_call(c: sqlite3.Row) -> dict:
    return {
        "to_service_name": c["to_service_name"],
        "call_kind": c["call_kind"],
        "reason": c["reason"],
        "data_needed": json.loads(c["data_needed"] or "[]"),
        "purpose_kind": c["purpose_kind"],
        "target_kind": c["target_kind"],
        "resource_type": c["resource_type"],
    }


def list_repositories(conn: sqlite3.Connection) -> dict:
    rows = repositories_repo.list_repositories(conn)
    return {
        "repositories": [
            {"name": r["name"], "root_path": r["root_path"], "service_count": r["service_count"]}
            for r in rows
        ]
    }


def list_services(conn: sqlite3.Connection) -> dict:
    rows = services_repo.list_services(conn)
    return {
        "services": [
            {"name": r["name"], "short_desc": r["short_desc"], "stack": r["stack"], "api_count": r["api_count"]}
            for r in rows
        ]
    }


def describe_service(conn: sqlite3.Connection, service: str, limit: int = DEFAULT_LIST_LIMIT, offset: int = 0) -> dict:
    error = _validate_pagination(limit, offset)
    if error:
        return {"error": error}
    row = services_repo.get_service_by_name(conn, service)
    if row is None:
        return {"error": f"unknown service: {service}"}
    calls, calls_page = _paginate(service_calls_repo.list_calls_for_service(conn, row["id"]), limit, offset)
    apis, apis_page = _paginate(apis_repo.list_apis(conn, row["id"]), limit, offset)
    components, components_page = _paginate(components_repo.list_components(conn, row["id"]), limit, offset)
    persistence, persists_page = _paginate(persistence_repo.list_persistence(conn, row["id"]), limit, offset)
    messages, messages_page = _paginate(messages_repo.list_messages(conn, row["id"]), limit, offset)
    return {
        "name": row["name"],
        "short_desc": row["short_desc"],
        "long_desc": row["long_desc"],
        "stack": row["stack"],
        "calls": [_fmt_call(c) for c in calls],
        "apis": [{"method": a["method"], "path": a["path"], "summary": a["summary"]} for a in apis],
        "components": [
            {"name": c["name"], "file_path": c["file_path"], "summary": c["summary"]} for c in components
        ],
        "persists": [{"name": p["name"], "kind": p["kind"], "engine": p["engine"]} for p in persistence],
        "messages": [
            {
                "direction": m["direction"], "channel": m["channel"], "provider": m["provider"],
                "description": m["description"],
            }
            for m in messages
        ],
        "freshness": compute_freshness(row["updated_at"], row["last_commit"], row["root_path"]),
        "pagination": {
            "limit": limit, "offset": offset,
            "calls": calls_page, "apis": apis_page, "components": components_page,
            "persists": persists_page, "messages": messages_page,
        },
    }


def list_apis(conn: sqlite3.Connection, service: str, limit: int = DEFAULT_LIST_LIMIT, offset: int = 0) -> dict:
    error = _validate_pagination(limit, offset)
    if error:
        return {"error": error}
    row = services_repo.get_service_by_name(conn, service)
    if row is None:
        return {"error": f"unknown service: {service}"}
    apis, page = _paginate(apis_repo.list_apis(conn, row["id"]), limit, offset)
    return {
        "apis": [{"method": a["method"], "path": a["path"], "summary": a["summary"]} for a in apis],
        **page,
    }


def describe_api(conn: sqlite3.Connection, service: str, method: str, path: str) -> dict:
    row = services_repo.get_service_by_name(conn, service)
    if row is None:
        return {"error": f"unknown service: {service}"}
    api = apis_repo.get_api_by_key(conn, row["id"], method.upper(), path)
    if api is None:
        return {"error": f"unknown api: {method} {path} on {service}"}
    calls = service_calls_repo.list_calls_for_api(conn, api["id"])
    validations = apis_repo.list_validations_for_api(conn, api["id"])
    return {
        "method": api["method"],
        "path": api["path"],
        "summary": api["summary"],
        "description": api["description"],
        "response_shape": json.loads(api["response_shape"] or "[]"),
        "request_shape": json.loads(api["request_shape"] or "[]"),
        "calls": [_fmt_call(c) for c in calls],
        "validations": [{"kind": v["kind"], "description": v["description"]} for v in validations],
    }


def list_entrypoints(conn: sqlite3.Connection, service: str, limit: int = DEFAULT_LIST_LIMIT, offset: int = 0) -> dict:
    """List every transport entry into a service without loading its flow bodies."""
    error = _validate_pagination(limit, offset)
    if error:
        return {"error": error}
    row = services_repo.get_service_by_name(conn, service)
    if row is None:
        return {"error": f"unknown service: {service}"}
    entrypoints, page = _paginate(flows_repo.list_entrypoints(conn, row["id"]), limit, offset)
    return {
        "entrypoints": [
            {
                "kind": entry["kind"], "method": entry["method"], "name": entry["name"],
                "symbol": entry["symbol"], "evidence": {
                    "file": entry["file_path"], "start_line": entry["start_line"], "end_line": entry["end_line"],
                },
            }
            for entry in entrypoints
        ],
        **page,
    }


def describe_entrypoint(conn: sqlite3.Connection, service: str, kind: str, method: str, name: str) -> dict:
    """Return a compact deterministic flow for one HTTP, GraphQL, message or CLI entrypoint."""
    row = services_repo.get_service_by_name(conn, service)
    if row is None:
        return {"error": f"unknown service: {service}"}
    entrypoint = flows_repo.get_entrypoint(conn, row["id"], kind, method, name)
    if entrypoint is None:
        return {"error": f"unknown entrypoint: {kind} {method} {name} on {service}"}
    edges = flows_repo.list_reachable_edges(conn, row["id"], entrypoint["symbol"])
    return {
        "entrypoint": {
            "kind": entrypoint["kind"], "method": entrypoint["method"], "name": entrypoint["name"],
            "symbol": entrypoint["symbol"], "evidence": {
                "file": entrypoint["file_path"], "start_line": entrypoint["start_line"], "end_line": entrypoint["end_line"],
            },
        },
        "flow": [
            {
                "from": edge["from_symbol"], "to": edge["to_symbol"], "kind": edge["kind"],
                "confidence": edge["confidence"], "origin": edge["origin"], "evidence": {
                    "file": edge["file_path"], "start_line": edge["start_line"], "end_line": edge["end_line"],
                },
            }
            for edge in edges
        ],
        "smells": find_entrypoint_smells(entrypoint, edges),
    }


def describe_persistence(conn: sqlite3.Connection, service: str, limit: int = DEFAULT_LIST_LIMIT, offset: int = 0) -> dict:
    error = _validate_pagination(limit, offset)
    if error:
        return {"error": error}
    row = services_repo.get_service_by_name(conn, service)
    if row is None:
        return {"error": f"unknown service: {service}"}
    entities, page = _paginate(persistence_repo.list_persistence(conn, row["id"]), limit, offset)
    return {
        "entities": [
            {
                "name": e["name"], "kind": e["kind"], "engine": e["engine"],
                "schema_json": json.loads(e["schema_json"] or "[]"),
            }
            for e in entities
        ],
        **page,
    }


def describe_messages(conn: sqlite3.Connection, service: str, limit: int = DEFAULT_LIST_LIMIT, offset: int = 0) -> dict:
    error = _validate_pagination(limit, offset)
    if error:
        return {"error": error}
    row = services_repo.get_service_by_name(conn, service)
    if row is None:
        return {"error": f"unknown service: {service}"}
    messages, page = _paginate(messages_repo.list_messages(conn, row["id"]), limit, offset)
    return {
        "messages": [
            {
                "direction": m["direction"],
                "channel": m["channel"],
                "provider": m["provider"],
                "shape_json": json.loads(m["shape_json"] or "[]"),
                "description": m["description"],
            }
            for m in messages
        ],
        **page,
    }


def search(conn: sqlite3.Connection, query: str) -> dict:
    return {"results": search_repo.search(conn, query)}


def _fmt_relationship_call(c: sqlite3.Row, *, direction: str, other_key: str, other_value: str) -> dict:
    return {
        "type": c["call_kind"].upper(),
        "direction": direction,
        other_key: other_value,
        "reason": c["reason"],
        "confidence": c["confidence"],
        "target_kind": c["target_kind"],
        "evidence": json.loads(c["evidence_json"] or "[]"),
        "provenance": infer_provenance(c["confidence"]),
    }


def _fmt_message_link(link: sqlite3.Row) -> dict:
    direction = "outbound" if link["local_direction"] == "publishes" else "inbound"
    other_key = "target_service" if direction == "outbound" else "source_service"
    return {
        "type": "MESSAGE_LINK",
        "direction": direction,
        "channel": link["channel"],
        other_key: link["other_service"],
        "reason": None,
        "confidence": None,
        "evidence": [],
        "provenance": infer_provenance(None),
    }


def get_relationships(conn: sqlite3.Connection, service: str, direction: str = "both") -> dict:
    """Fact + semantic-interpretation edges around one service: outbound calls it
    makes, inbound calls other services make into it, and queue/topic links inferred
    from matching publish/consume channel names."""
    row = services_repo.get_service_by_name(conn, service)
    if row is None:
        return {"error": f"unknown service: {service}"}

    relationships: list[dict] = []
    if direction in ("outbound", "both"):
        for c in service_calls_repo.list_calls_for_service(conn, row["id"]):
            relationships.append(
                _fmt_relationship_call(c, direction="outbound", other_key="target_service", other_value=c["to_service_name"])
            )
    if direction in ("inbound", "both"):
        for c in service_calls_repo.list_inbound_calls(conn, row["id"]):
            relationships.append(
                _fmt_relationship_call(c, direction="inbound", other_key="source_service", other_value=c["from_service_name"])
            )

    for link in messages_repo.list_message_links(conn, row["id"]):
        local_is_outbound = link["local_direction"] == "publishes"
        if direction == "both" or (direction == "outbound" and local_is_outbound) or (direction == "inbound" and not local_is_outbound):
            relationships.append(_fmt_message_link(link))

    return {"service": service, "relationships": relationships}


def _outgoing_edges(conn: sqlite3.Connection, service_row: sqlite3.Row) -> list[dict]:
    edges = []
    for c in service_calls_repo.list_calls_for_service(conn, service_row["id"]):
        edges.append(
            {
                "to": c["to_service_name"],
                "type": c["call_kind"].upper(),
                "reason": c["reason"],
                "confidence": c["confidence"],
                "evidence": json.loads(c["evidence_json"] or "[]"),
            }
        )
    for link in messages_repo.list_message_links(conn, service_row["id"]):
        if link["local_direction"] == "publishes":
            edges.append(
                {
                    "to": link["other_service"],
                    "type": "MESSAGE_LINK",
                    "reason": f"channel: {link['channel']}",
                    "confidence": None,
                    "evidence": [],
                }
            )
    return edges


def trace_flow(conn: sqlite3.Connection, from_service: str, to_service: str, max_hops: int = 6) -> dict:
    """Shortest directed path from one service to another, walking outbound calls and
    publish->consume message links — the multi-hop counterpart to get_relationships'
    single hop. Facts + semantic reasons per hop, same as get_relationships."""
    from_row = services_repo.get_service_by_name(conn, from_service)
    if from_row is None:
        return {"error": f"unknown service: {from_service}"}
    if services_repo.get_service_by_name(conn, to_service) is None:
        return {"error": f"unknown service: {to_service}"}
    if from_service == to_service:
        return {"path": [], "reachable": True, "hops": 0, "note": "from and to are the same service"}

    visited = {from_service}
    queue = deque([(from_service, [])])
    while queue:
        current, path = queue.popleft()
        if len(path) >= max_hops:
            continue
        current_row = services_repo.get_service_by_name(conn, current)
        for edge in _outgoing_edges(conn, current_row):
            hop = {"from": current, **edge}
            new_path = path + [hop]
            if hop["to"] == to_service:
                return {"path": new_path, "reachable": True, "hops": len(new_path)}
            if hop["to"] not in visited and services_repo.get_service_by_name(conn, hop["to"]) is not None:
                visited.add(hop["to"])
                queue.append((hop["to"], new_path))

    return {"path": [], "reachable": False, "note": f"no path found within {max_hops} hops"}


def find_architecture_smells(conn: sqlite3.Connection) -> dict:
    run_id = architecture_repo.latest_run_id(conn)
    if run_id is None:
        return {"findings": [], "run_id": None, "note": "no architecture run yet — index at least one service first"}
    findings = architecture_repo.list_findings(conn, run_id)
    response = {
        "run_id": run_id,
        "findings": [
            {
                "kind": f["kind"],
                "severity": f["severity"],
                "services": json.loads(f["services_json"]),
                "detail": json.loads(f["detail_json"] or "{}"),
                "reason": f["reason"],
            }
            for f in findings
        ],
    }
    previous_run_id = architecture_repo.previous_run_id(conn, run_id)
    if previous_run_id is not None:
        # Omitted entirely (not an empty/null trend) on the very first run ever —
        # there's nothing honest to compare against yet, same "don't fabricate when
        # there's nothing to say" convention as find_change_surface's own fields.
        response["trend"] = diff_architecture_runs(conn, previous_run_id, run_id)
    return response


def find_change_surface(
    conn: sqlite3.Connection, backend: LLMBackend, task: str, hint_services: list[str] | None = None
) -> dict:
    """Task/epic -> likely change surface, computed from the already-indexed System
    Knowledge Model (no source file is read here). This is a task inference, not a
    fact: every finding carries reason + confidence + evidence."""
    return change_surface.analyze_change_surface(conn, task, backend, hint_services)


def record_change_surface_feedback(conn: sqlite3.Connection, run_id: int, service: str, outcome: str) -> dict:
    """Closes the loop on a past find_change_surface call: report whether a finding
    was actually confirmed (you changed that service) or rejected (it wasn't needed).
    Future find_change_surface confidence for this service is nudged by this history
    (see generation.change_surface._recalibrate_confidence)."""
    if outcome not in ("confirmed", "rejected"):
        return {"error": f"invalid outcome: {outcome!r} (expected 'confirmed' or 'rejected')"}
    run = change_surface_repo.get_change_surface_run(conn, run_id)
    if run is None:
        return {"error": f"unknown change surface run_id: {run_id}"}
    findings = change_surface_repo.list_change_surface_findings(conn, run_id)
    if not any(f["service"] == service for f in findings):
        return {"error": f"service {service!r} was not part of run {run_id}"}
    change_surface_repo.record_change_surface_feedback(conn, run_id, service, outcome)
    return {"ok": True}


def verify_change_surface(conn: sqlite3.Connection, run_id: int, repository: str, since_commit: str) -> dict:
    """Read-only comparison of a past find_change_surface run against what a
    repository's commits actually changed since a given commit (git ground truth).
    Never records feedback itself — call record_change_surface_feedback separately
    if you want this comparison to influence future confidence."""
    return _verify_change_surface(conn, run_id, repository, since_commit, record_feedback=False)
