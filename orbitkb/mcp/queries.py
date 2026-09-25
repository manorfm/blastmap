"""Read helpers backing the MCP tools. Thin JSON-shaping wrappers over the
db.repositories.* modules (and, for find_change_surface, over generation.change_surface)."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections import deque
from pathlib import Path

from orbitkb.analysis.smells import find_entrypoint_smells
from orbitkb.db.repositories import apis as apis_repo
from orbitkb.db.repositories import architecture as architecture_repo
from orbitkb.db.repositories import change_plans as change_plans_repo
from orbitkb.db.repositories import change_surface as change_surface_repo
from orbitkb.db.repositories import cloud_iac as cloud_iac_repo
from orbitkb.db.repositories import components as components_repo
from orbitkb.db.repositories import context_telemetry as context_telemetry_repo
from orbitkb.db.repositories import flows as flows_repo
from orbitkb.db.repositories import (
    kubernetes_configuration as kubernetes_configuration_repo,
)
from orbitkb.db.repositories import messages as messages_repo
from orbitkb.db.repositories import persistence as persistence_repo
from orbitkb.db.repositories import repositories as repositories_repo
from orbitkb.db.repositories import runtime_evidence as runtime_evidence_repo
from orbitkb.db.repositories import search as search_repo
from orbitkb.db.repositories import security_findings as security_findings_repo
from orbitkb.db.repositories import service_calls as service_calls_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.discovery.hashing import git_working_changed_files_with_status
from orbitkb.generation import change_surface
from orbitkb.generation.architecture import diff_architecture_runs
from orbitkb.generation.backend_base import LLMBackend
from orbitkb.generation.change_assessment import assess_change_units
from orbitkb.generation.change_context import MAX_CONTEXT_SERVICES, build_change_context
from orbitkb.generation.change_plan import (
    derive_change_units,
    derive_decision_points,
    derive_error_mapping_review_units,
    derive_feature_flag_review_units,
    derive_persistence_migration_review_units,
    derive_runtime_configuration_mismatch_review_units,
    derive_runtime_configuration_review_units,
    derive_runtime_configuration_source_import_unknown_review_units,
    derive_runtime_configuration_source_unknown_review_units,
    validate_decision_selections,
)
from orbitkb.generation.freshness import compute_freshness
from orbitkb.generation.provenance import infer_provenance
from orbitkb.generation.verification import (
    verify_change_surface as _verify_change_surface,
)
from orbitkb.generation.verification import (
    verify_context_budget as _verify_context_budget,
)

# Progressive-disclosure budget for list-shaped MCP responses (describe_service's own
# lists, list_apis, describe_persistence, describe_messages): a real service can have
# far more endpoints/entities/messages than a benchmark fixture, so every such list is
# capped by default instead of returned whole — see README's context-efficiency notes.
DEFAULT_LIST_LIMIT = 50
MAX_RUNTIME_SOURCES_PER_CONFIGURATION_BINDING = 3
MAX_LIST_LIMIT = 500
DEFAULT_FLOW_EDGE_LIMIT = 50
MAX_FLOW_EDGE_LIMIT = 200
DEFAULT_PLAN_TOKEN_BUDGET = 2200
MAX_PLAN_TOKEN_BUDGET = 2200
_FLOW_KINDS = {"invokes", "injects", "validates", "reads", "writes", "publishes", "consumes"}
_EPIC_TYPE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
logger = logging.getLogger(__name__)


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


def _resolve_service(
    conn: sqlite3.Connection, service: str, repository: str | None,
) -> tuple[sqlite3.Row | None, dict | None]:
    """Resolve a service without silently selecting a same-named repository peer."""
    if repository is not None:
        repo = repositories_repo.get_repository_by_name(conn, repository)
        if repo is None:
            return None, {"error": f"unknown repository: {repository}"}
        row = services_repo.get_service_by_name(conn, service, repository_id=repo["id"])
        return (row, None) if row is not None else (None, {"error": f"unknown service: {service} on {repository}"})
    candidates = services_repo.list_service_candidates_by_name(conn, service)
    if not candidates:
        return None, {"error": f"unknown service: {service}"}
    if len(candidates) > 1:
        return None, {
            "error": f"ambiguous service: {service}; specify repository",
            "repositories": [candidate["repository_name"] for candidate in candidates],
        }
    return candidates[0], None


def _resolve_static_service_call_target(
    conn: sqlite3.Connection,
    caller: sqlite3.Row,
    call: sqlite3.Row,
    cache: dict[tuple[object, ...], dict],
) -> dict:
    """Resolve a literal static target without choosing among repository peers."""
    key = (
        caller["repository_id"], call["target_service"], call["protocol"],
        call["target_method"], call["target_path"],
    )
    if key in cache:
        return cache[key]
    target, candidates = services_repo.resolve_service_reference(
        conn, call["target_service"], caller["repository_id"],
    )
    if target is None:
        resolution = (
            {"status": "not_indexed"}
            if not candidates
            else {
                "status": "ambiguous",
                "repositories": sorted({candidate["repository_name"] or "standalone" for candidate in candidates}),
            }
        )
        cache[key] = resolution
        return resolution

    resolution: dict = {
        "status": "service_indexed", "service": target["name"],
        "repository": target["repository_name"],
    }
    if call["protocol"] == "http" and isinstance(call["target_method"], str) and isinstance(call["target_path"], str):
        entrypoint = flows_repo.get_entrypoint(
            conn, target["id"], "http", call["target_method"], call["target_path"],
        )
        if entrypoint is not None:
            resolution["status"] = "endpoint_indexed"
            resolution["entrypoint"] = {
                "kind": entrypoint["kind"], "method": entrypoint["method"],
                "name": entrypoint["name"], "symbol": entrypoint["symbol"],
                "evidence": {
                    "file": entrypoint["file_path"], "start_line": entrypoint["start_line"],
                    "end_line": entrypoint["end_line"],
                },
            }
    cache[key] = resolution
    return resolution


def list_repositories(conn: sqlite3.Connection) -> dict:
    rows = repositories_repo.list_repositories(conn)
    return {
        "repositories": [
            {"name": r["name"], "root_path": r["root_path"], "service_count": r["service_count"]}
            for r in rows
        ]
    }


def list_services(conn: sqlite3.Connection, repository: str | None = None) -> dict:
    repo_id = None
    if repository is not None:
        repo = repositories_repo.get_repository_by_name(conn, repository)
        if repo is None:
            return {"error": f"unknown repository: {repository}"}
        repo_id = repo["id"]
    rows = services_repo.list_services(conn, repo_id)
    return {
        "services": [
            {
                "name": r["name"], "repository": r["repository_name"],
                "short_desc": r["short_desc"], "stack": r["stack"], "api_count": r["api_count"],
            }
            for r in rows
        ]
    }


def describe_service(
    conn: sqlite3.Connection,
    service: str,
    limit: int = DEFAULT_LIST_LIMIT,
    offset: int = 0,
    repository: str | None = None,
) -> dict:
    error = _validate_pagination(limit, offset)
    if error:
        return {"error": error}
    row, service_error = _resolve_service(conn, service, repository)
    if service_error:
        return service_error
    calls, calls_page = _paginate(service_calls_repo.list_calls_for_service(conn, row["id"]), limit, offset)
    apis, apis_page = _paginate(apis_repo.list_apis(conn, row["id"]), limit, offset)
    components, components_page = _paginate(components_repo.list_components(conn, row["id"]), limit, offset)
    persistence, persists_page = _paginate(persistence_repo.list_persistence(conn, row["id"]), limit, offset)
    messages, messages_page = _paginate(messages_repo.list_messages(conn, row["id"]), limit, offset)
    return {
        "name": row["name"],
        "repository": row["repository_name"],
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


def list_apis(
    conn: sqlite3.Connection, service: str, limit: int = DEFAULT_LIST_LIMIT, offset: int = 0,
    repository: str | None = None,
) -> dict:
    error = _validate_pagination(limit, offset)
    if error:
        return {"error": error}
    row, service_error = _resolve_service(conn, service, repository)
    if service_error:
        return service_error
    apis, page = _paginate(apis_repo.list_apis(conn, row["id"]), limit, offset)
    return {
        "service": row["name"], "repository": row["repository_name"],
        "apis": [{"method": a["method"], "path": a["path"], "summary": a["summary"]} for a in apis],
        **page,
    }


def describe_api(conn: sqlite3.Connection, service: str, method: str, path: str, repository: str | None = None) -> dict:
    row, service_error = _resolve_service(conn, service, repository)
    if service_error:
        return service_error
    api = apis_repo.get_api_by_key(conn, row["id"], method.upper(), path)
    if api is None:
        return {"error": f"unknown api: {method} {path} on {service}"}
    calls = service_calls_repo.list_calls_for_api(conn, api["id"])
    validations = apis_repo.list_validations_for_api(conn, api["id"])
    return {
        "service": row["name"], "repository": row["repository_name"],
        "method": api["method"],
        "path": api["path"],
        "summary": api["summary"],
        "description": api["description"],
        "response_shape": json.loads(api["response_shape"] or "[]"),
        "request_shape": json.loads(api["request_shape"] or "[]"),
        "calls": [_fmt_call(c) for c in calls],
        "validations": [{"kind": v["kind"], "description": v["description"]} for v in validations],
    }


def list_entrypoints(
    conn: sqlite3.Connection, service: str, limit: int = DEFAULT_LIST_LIMIT, offset: int = 0,
    repository: str | None = None,
) -> dict:
    """List every transport entry into a service without loading its flow bodies."""
    error = _validate_pagination(limit, offset)
    if error:
        return {"error": error}
    row, service_error = _resolve_service(conn, service, repository)
    if service_error:
        return service_error
    entrypoints, page = _paginate(flows_repo.list_entrypoints(conn, row["id"]), limit, offset)
    return {
        "service": row["name"], "repository": row["repository_name"], "entrypoints": [
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


def list_security_findings(conn: sqlite3.Connection, service: str, repository: str | None = None) -> dict:
    """Return security findings without exposing source excerpts or secret values."""
    row, service_error = _resolve_service(conn, service, repository)
    if service_error:
        return service_error
    return {
        "service": row["name"], "repository": row["repository_name"], "findings": [
            {"kind": item["kind"], "severity": item["severity"], "file": item["file_path"], "line": item["line"], "reason": item["reason"]}
            for item in security_findings_repo.list_findings(conn, row["id"])
        ]
    }


def describe_entrypoint(
    conn: sqlite3.Connection,
    service: str,
    kind: str,
    method: str,
    name: str,
    max_edges: int = DEFAULT_FLOW_EDGE_LIMIT,
    repository: str | None = None,
) -> dict:
    """Return a compact deterministic flow for one HTTP, GraphQL, message or CLI entrypoint."""
    if max_edges < 1:
        return {"error": f"max_edges must be >= 1 (got {max_edges})"}
    row, service_error = _resolve_service(conn, service, repository)
    if service_error:
        return service_error
    entrypoint = flows_repo.get_entrypoint(conn, row["id"], kind, method, name)
    if entrypoint is None:
        return {"error": f"unknown entrypoint: {kind} {method} {name} on {service}"}
    effective_max_edges = min(max_edges, MAX_FLOW_EDGE_LIMIT)
    bounded_edges = flows_repo.list_reachable_edges(conn, row["id"], entrypoint["symbol"], effective_max_edges + 1)
    truncated = len(bounded_edges) > effective_max_edges
    edges = bounded_edges[:effective_max_edges]
    flow_symbols = {entrypoint["symbol"]} | {edge["from_symbol"] for edge in edges} | {edge["to_symbol"] for edge in edges}
    static_service_calls = flows_repo.list_static_service_calls_for_sources(conn, row["id"], flow_symbols)
    resilience_policies = flows_repo.list_static_resilience_policies_for_sources(conn, row["id"], flow_symbols)
    target_cache: dict[tuple[object, ...], dict] = {}
    return {
        "service": row["name"], "repository": row["repository_name"],
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
        "flow_pagination": {"max_edges": effective_max_edges, "truncated": truncated},
        "persistence_operations": [
            {
                "operation": edge["kind"], "target": edge["to_symbol"], "evidence": {
                    "file": edge["file_path"], "start_line": edge["start_line"], "end_line": edge["end_line"],
                },
            }
            for edge in edges
            if edge["kind"] in {"reads", "writes"}
        ],
        "boundaries": [
            {"source": item["source"], "kind": item["kind"], "evidence": {
                "file": item["file_path"], "start_line": item["start_line"], "end_line": item["end_line"],
            }}
            for item in flows_repo.list_flow_boundaries(conn, row["id"], flow_symbols)
        ],
        "error_contracts": [
            {
                "source": item["source"], "role": item["role"], "error_kind": item["error_kind"],
                "internal_type": item["internal_type"], "protocol": item["protocol"],
                "transport_code": item["transport_code"], "public_code": item["public_code"],
                "exposes_internal_detail": bool(item["exposes_internal_detail"]),
                "retryability": item["retryability"], "evidence": {
                    "file": item["file_path"], "start_line": item["start_line"], "end_line": item["end_line"],
                },
            }
            for item in flows_repo.list_static_error_contracts_for_sources(conn, row["id"], flow_symbols)
        ],
        "service_calls": [
            {
                "source": item["source"], "target_service": item["target_service"],
                "protocol": item["protocol"], "method": item["target_method"],
                "path": item["target_path"], "evidence": {
                    "file": item["file_path"], "start_line": item["start_line"],
                    "end_line": item["end_line"],
                },
                "resolved_target": _resolve_static_service_call_target(conn, row, item, target_cache),
            }
            for item in static_service_calls
        ],
        "resilience_policies": [
            {
                "source": item["source"], "kind": item["kind"],
                "mechanism": item["mechanism"], "value": item["value"],
                "unit": item["unit"], "evidence": {
                    "file": item["file_path"], "start_line": item["start_line"],
                    "end_line": item["end_line"],
                },
            }
            for item in resilience_policies
        ],
        "contract": flows_repo.get_entrypoint_contract(conn, entrypoint["id"]),
        "smells": find_entrypoint_smells(entrypoint, edges),
    }


def describe_error_flow(
    conn: sqlite3.Connection, service: str, kind: str, method: str, name: str,
    repository: str | None = None,
) -> dict:
    """Describe only exact HTTP error mappings reached from one entrypoint.

    A flow needs a source-proven HTTP call, an indexed target endpoint with an
    explicit HTTP error contract, and a reachable caller mapping with the same
    declared error identity. Missing evidence stays in ``unknowns`` rather than
    becoming an assumed 500 or an assumed propagation.
    """
    row, service_error = _resolve_service(conn, service, repository)
    if service_error:
        return service_error
    entrypoint = flows_repo.get_entrypoint(conn, row["id"], kind, method, name)
    if entrypoint is None:
        return {"error": f"unknown entrypoint: {kind} {method} {name} on {service}"}
    caller_edges = flows_repo.list_reachable_edges(conn, row["id"], entrypoint["symbol"], MAX_FLOW_EDGE_LIMIT)
    caller_symbols = {entrypoint["symbol"]} | {
        symbol for edge in caller_edges for symbol in (edge["from_symbol"], edge["to_symbol"])
    }
    caller_contracts = flows_repo.list_static_error_contracts_for_sources(conn, row["id"], caller_symbols)
    calls = flows_repo.list_static_service_calls_for_sources(conn, row["id"], caller_symbols)
    mappings = [
        contract
        for contract in caller_contracts
        if contract["role"] in {"maps", "handles"}
        and contract["protocol"] == "http"
        and contract["transport_code"] is not None
    ]
    flows: list[dict] = []
    unknowns: list[str] = []
    target_cache: dict[tuple[object, ...], dict] = {}
    for call in calls:
        if call["protocol"] != "http":
            unknowns.append(f"{call['target_service']} {call['protocol']} error flow is not supported yet.")
            continue
        resolution = _resolve_static_service_call_target(conn, row, call, target_cache)
        if resolution["status"] != "endpoint_indexed":
            unknowns.append(
                f"{call['target_service']} {call['protocol']} target is not resolved to an indexed endpoint."
            )
            continue
        target, _candidates = services_repo.resolve_service_reference(
            conn, call["target_service"], row["repository_id"],
        )
        if target is None:
            continue
        target_entrypoint = flows_repo.get_entrypoint(
            conn, target["id"], "http", call["target_method"], call["target_path"],
        )
        if target_entrypoint is None:
            continue
        target_edges = flows_repo.list_reachable_edges(
            conn, target["id"], target_entrypoint["symbol"], MAX_FLOW_EDGE_LIMIT,
        )
        target_symbols = {target_entrypoint["symbol"]} | {
            symbol for edge in target_edges for symbol in (edge["from_symbol"], edge["to_symbol"])
        }
        origins = [
            contract
            for contract in flows_repo.list_static_error_contracts_for_sources(conn, target["id"], target_symbols)
            if contract["protocol"] == "http" and contract["transport_code"] is not None
        ]
        if not origins:
            unknowns.append(
                f"{target['name']} {call['target_method']} {call['target_path']} has no reachable indexed HTTP error contract."
            )
            continue
        for origin in origins:
            matching_mappings = [mapping for mapping in mappings if _same_error_identity(origin, mapping)]
            if not matching_mappings:
                unknowns.append(
                    f"{call['source']} has no reachable mapping for {target['name']} {origin['transport_code']} {origin['internal_type'] or origin['public_code'] or origin['error_kind']}."
                )
                continue
            for mapping in matching_mappings:
                flows.append(_error_flow(row, call, target, origin, mapping))
    return {
        "service": row["name"],
        "repository": row["repository_name"],
        "entrypoint": {
            "kind": entrypoint["kind"], "method": entrypoint["method"],
            "name": entrypoint["name"], "symbol": entrypoint["symbol"],
        },
        "error_flows": flows,
        "unknowns": sorted(set(unknowns)),
    }


def _same_error_identity(origin: sqlite3.Row, mapping: sqlite3.Row) -> bool:
    """Join errors only through a declared type or public code, never broad kind."""
    if origin["internal_type"] and mapping["internal_type"]:
        return origin["internal_type"] == mapping["internal_type"]
    return bool(origin["public_code"] and origin["public_code"] == mapping["public_code"])


def _error_flow(
    caller: sqlite3.Row, call: sqlite3.Row, target: sqlite3.Row,
    origin: sqlite3.Row, mapping: sqlite3.Row,
) -> dict:
    origin_evidence = _error_evidence(origin)
    mapping_evidence = _error_evidence(mapping)
    call_evidence = _call_evidence(call)
    status = mapping["transport_code"]
    return {
        "origin": {
            "service": target["name"], "symbol": origin["source"],
            "transport": {
                "protocol": origin["protocol"], "status": origin["transport_code"],
                "public_code": origin["public_code"],
            },
            "evidence": origin_evidence,
        },
        "handling": [{
            "service": caller["name"], "symbol": mapping["source"], "action": f"maps_to_http_{status}",
            "evidence": mapping_evidence,
        }],
        "outcome": {
            "protocol": mapping["protocol"], "status": status, "public_code": mapping["public_code"],
        },
        "confidence": 1.0,
        "evidence": _unique_evidence(call_evidence, origin_evidence, mapping_evidence),
    }


def _error_evidence(contract: sqlite3.Row) -> dict:
    return {
        "file": contract["file_path"], "start_line": contract["start_line"], "end_line": contract["end_line"],
    }


def _call_evidence(call: sqlite3.Row) -> dict:
    return {"file": call["file_path"], "start_line": call["start_line"], "end_line": call["end_line"]}


def _unique_evidence(*items: dict) -> list[dict]:
    """Keep provenance complete without repeating one source location in MCP output."""
    return list({(item["file"], item["start_line"], item["end_line"]): item for item in items}.values())


def ingest_runtime_evidence(
    conn: sqlite3.Connection, service: str, source: str, observations: list[dict], repository: str | None = None,
) -> dict:
    """Ingest normalized runtime edges, never trace IDs, attributes, payloads or source."""
    row, error = _resolve_service(conn, service, repository)
    if error:
        return error
    if source not in {"otel", "broker"}:
        return {"error": "source must be 'otel' or 'broker'"}
    accepted = rejected = 0
    for item in observations:
        if not _valid_runtime_observation(item):
            rejected += 1
            continue
        runtime_evidence_repo.upsert_observation(conn, row["id"], source, item)
        accepted += 1
    return {"accepted": accepted, "rejected": rejected}


def _valid_runtime_observation(item: object) -> bool:
    return (
        isinstance(item, dict) and set(item) == {"from", "to", "kind", "count"}
        and isinstance(item["from"], str) and isinstance(item["to"], str)
        and len(item["from"]) <= 256 and len(item["to"]) <= 256
        and item["kind"] in _FLOW_KINDS and isinstance(item["count"], int) and item["count"] > 0
    )


def describe_runtime_divergence(conn: sqlite3.Connection, service: str, repository: str | None = None) -> dict:
    """Compare runtime observations with static edges without conflating provenance."""
    row, error = _resolve_service(conn, service, repository)
    if error:
        return error
    observed = {(item["from_symbol"], item["to_symbol"], item["kind"]): item["observed_count"]
                for item in runtime_evidence_repo.list_observations(conn, row["id"])}
    static = {(item["from_symbol"], item["to_symbol"], item["kind"])
              for item in flows_repo.list_flow_edges(conn, row["id"])}
    return {
        "service": row["name"], "repository": row["repository_name"],
        "observed_only": [{"from": edge[0], "to": edge[1], "kind": edge[2], "count": observed[edge]}
                          for edge in sorted(observed.keys() - static)],
        "static_unobserved": [{"from": edge[0], "to": edge[1], "kind": edge[2]}
                              for edge in sorted(static - observed.keys())],
        "unknowns": ["A static edge not observed at runtime is not proof of dead code; coverage and sampling may be incomplete."],
    }


def describe_persistence(
    conn: sqlite3.Connection, service: str, limit: int = DEFAULT_LIST_LIMIT, offset: int = 0,
    repository: str | None = None,
) -> dict:
    error = _validate_pagination(limit, offset)
    if error:
        return {"error": error}
    row, service_error = _resolve_service(conn, service, repository)
    if service_error:
        return service_error
    entities, page = _paginate(persistence_repo.list_persistence(conn, row["id"]), limit, offset)
    migration_facts, migration_page = _paginate(
        flows_repo.list_static_migration_facts(conn, row["id"]), limit, offset,
    )
    return {
        "service": row["name"], "repository": row["repository_name"], "entities": [
            {
                "name": e["name"], "kind": e["kind"], "engine": e["engine"],
                "schema_json": json.loads(e["schema_json"] or "[]"),
            }
            for e in entities
        ],
        "static_facts": [
            {"name": item["name"], "kind": item["kind"], "owner": item["owner"], "evidence": {
                "file": item["file_path"], "start_line": item["start_line"], "end_line": item["end_line"],
            }}
            for item in flows_repo.list_static_persistence_facts(conn, row["id"])
        ],
        "migration_facts": [
            {
                "operation": item["operation"], "table_name": item["table_name"],
                "column_name": item["column_name"], "destructive": bool(item["destructive"]),
                "evidence": {
                    "file": item["file_path"], "start_line": item["start_line"], "end_line": item["end_line"],
                },
            }
            for item in migration_facts
        ],
        "migration_pagination": migration_page,
        **page,
    }


def describe_configuration(
    conn: sqlite3.Connection, service: str, limit: int = DEFAULT_LIST_LIMIT, offset: int = 0,
    repository: str | None = None,
) -> dict:
    """Return source-proven configuration-key reads without values or resolution claims."""
    error = _validate_pagination(limit, offset)
    if error:
        return {"error": error}
    row, service_error = _resolve_service(conn, service, repository)
    if service_error:
        return service_error
    bindings, page = _paginate(
        flows_repo.list_static_configuration_bindings(conn, row["id"]), limit, offset,
    )
    environment_keys = {item["key"] for item in bindings if item["kind"] == "environment"}
    runtime_sources_by_key: dict[str, list[sqlite3.Row]] = {}
    for runtime_binding in kubernetes_configuration_repo.list_kubernetes_configuration_bindings_for_environment_keys(
        conn, row["id"], environment_keys,
    ):
        runtime_sources_by_key.setdefault(runtime_binding["environment_key"], []).append(runtime_binding)
    response_bindings = []
    for item in bindings:
        response_binding = {
            "source": item["source"], "key": item["key"], "kind": item["kind"],
            "sensitive": bool(item["sensitive"]), "evidence": {
                "file": item["file_path"], "start_line": item["start_line"],
                "end_line": item["end_line"],
            },
        }
        runtime_sources = runtime_sources_by_key.get(item["key"], [])
        if item["kind"] == "environment" and runtime_sources:
            response_binding["runtime_sources"] = {
                "count": len(runtime_sources),
                "references": [_runtime_configuration_reference(source) for source in runtime_sources[
                    :MAX_RUNTIME_SOURCES_PER_CONFIGURATION_BINDING
                ]],
                "truncated": len(runtime_sources) > MAX_RUNTIME_SOURCES_PER_CONFIGURATION_BINDING,
            }
        response_bindings.append(response_binding)
    return {
        "service": row["name"], "repository": row["repository_name"],
        "bindings": response_bindings,
        **page,
    }


def describe_runtime_configuration(
    conn: sqlite3.Connection, service: str, limit: int = DEFAULT_LIST_LIMIT, offset: int = 0,
    repository: str | None = None,
) -> dict:
    """Return source-proven Kubernetes configuration references without values."""
    error = _validate_pagination(limit, offset)
    if error:
        return {"error": error}
    row, service_error = _resolve_service(conn, service, repository)
    if service_error:
        return service_error
    bindings, page = _paginate(
        kubernetes_configuration_repo.list_kubernetes_configuration_bindings_for_service(conn, row["id"]),
        limit, offset,
    )
    source_imports, source_import_page = _paginate(
        kubernetes_configuration_repo.list_kubernetes_configuration_source_imports_for_service(conn, row["id"]),
        limit, offset,
    )
    source_import_unknowns_by_reference = {
        (
            unknown["source_kind"], unknown["source_name"], unknown["prefix"],
            unknown["reference_file_path"], unknown["reference_start_line"], unknown["reference_end_line"],
        )
        for unknown in kubernetes_configuration_repo.list_kubernetes_configuration_source_import_unknowns_for_service(
            conn, row["id"],
        )
    }
    mismatches_by_reference = {
        (
            mismatch["environment_key"], mismatch["source_kind"], mismatch["source_name"], mismatch["source_key"],
            mismatch["reference_file_path"], mismatch["reference_start_line"], mismatch["reference_end_line"],
        ): mismatch
        for mismatch in kubernetes_configuration_repo.list_kubernetes_configuration_key_mismatches_for_service(
            conn, row["id"],
        )
    }
    unknowns_by_reference = {
        (
            unknown["environment_key"], unknown["source_kind"], unknown["source_name"], unknown["source_key"],
            unknown["reference_file_path"], unknown["reference_start_line"], unknown["reference_end_line"],
        ): unknown
        for unknown in kubernetes_configuration_repo.list_kubernetes_configuration_source_unknowns_for_service(
            conn, row["id"],
        )
    }
    response_bindings = []
    for item in bindings:
        response_binding = {"environment_key": item["environment_key"], **_runtime_configuration_reference(item)}
        mismatch = mismatches_by_reference.get((
            item["environment_key"], item["source_kind"], item["source_name"], item["source_key"],
            item["file_path"], item["start_line"], item["end_line"],
        ))
        if mismatch is not None:
            response_binding["declaration"] = {
                "status": "key_not_declared",
                "evidence": {
                    "file": mismatch["declaration_file_path"],
                    "start_line": mismatch["declaration_start_line"],
                    "end_line": mismatch["declaration_end_line"],
                },
            }
        elif (
            item["environment_key"], item["source_kind"], item["source_name"], item["source_key"],
            item["file_path"], item["start_line"], item["end_line"],
        ) in unknowns_by_reference:
            response_binding["declaration"] = {"status": "not_declared_locally"}
        response_bindings.append(response_binding)
    response = {
        "service": row["name"], "repository": row["repository_name"],
        "bindings": response_bindings,
        **page,
    }
    if source_imports:
        response_source_imports = [
            _runtime_configuration_source_import(item, source_import_unknowns_by_reference)
            for item in source_imports
        ]
        response["source_imports"] = response_source_imports
        response["source_import_total"] = source_import_page["total"]
        response["source_import_truncated"] = source_import_page["truncated"]
        response["unknowns"] = [
            "envFrom imports source keys without explicit per-key references; exact environment keys are not indexed.",
        ]
        if any(item.get("declaration", {}).get("status") == "not_declared_locally" for item in response_source_imports):
            response["unknowns"].append(
                "An envFrom source without a local declaration may be managed by another repository, chart, controller, or deployment process.",
            )
    return response


def _runtime_configuration_reference(item: sqlite3.Row) -> dict:
    return {
        "source": {
            "kind": item["source_kind"], "name": item["source_name"], "key": item["source_key"],
        },
        "workload": {
            "kind": item["workload_kind"], "name": item["workload_name"], "container": item["container_name"],
        },
        "evidence": {
            "file": item["file_path"], "start_line": item["start_line"], "end_line": item["end_line"],
        },
    }


def _runtime_configuration_source_import(
    item: sqlite3.Row, source_import_unknowns_by_reference: set[tuple[str, str, str | None, str, int, int]],
) -> dict:
    """Shape an ``envFrom`` source while preserving its intentionally unknown keys."""
    workload = {
        "kind": item["workload_kind"], "name": item["workload_name"], "container": item["container_name"],
    }
    if item["container_role"] is not None:
        workload["container_role"] = item["container_role"]
    response = {
        "source": {"kind": item["source_kind"], "name": item["source_name"]},
        "workload": workload,
        "evidence": {"file": item["file_path"], "start_line": item["start_line"], "end_line": item["end_line"]},
        "key_coverage": "unknown",
    }
    if item["prefix"] is not None:
        response["prefix"] = item["prefix"]
    if item["optional"] is not None:
        response["availability"] = "optional" if item["optional"] else "required"
    if (
        item["source_kind"], item["source_name"], item["prefix"],
        item["file_path"], item["start_line"], item["end_line"],
    ) in source_import_unknowns_by_reference:
        response["declaration"] = {"status": "not_declared_locally"}
    return response


def describe_feature_flags(
    conn: sqlite3.Connection, service: str, limit: int = DEFAULT_LIST_LIMIT, offset: int = 0,
    repository: str | None = None,
) -> dict:
    """Return source-proven feature-flag reads without values or rollout claims."""
    error = _validate_pagination(limit, offset)
    if error:
        return {"error": error}
    row, service_error = _resolve_service(conn, service, repository)
    if service_error:
        return service_error
    flags, page = _paginate(flows_repo.list_static_feature_flags(conn, row["id"]), limit, offset)
    return {
        "service": row["name"], "repository": row["repository_name"],
        "flags": [
            {
                "source": item["source"], "key": item["key"], "provider": item["provider"],
                "evidence": {
                    "file": item["file_path"], "start_line": item["start_line"],
                    "end_line": item["end_line"],
                },
            }
            for item in flags
        ],
        **page,
    }


def describe_messages(
    conn: sqlite3.Connection, service: str, limit: int = DEFAULT_LIST_LIMIT, offset: int = 0,
    repository: str | None = None,
) -> dict:
    error = _validate_pagination(limit, offset)
    if error:
        return {"error": error}
    row, service_error = _resolve_service(conn, service, repository)
    if service_error:
        return service_error
    messages, page = _paginate(messages_repo.list_messages(conn, row["id"]), limit, offset)
    return {
        "service": row["name"], "repository": row["repository_name"], "messages": [
            {
                "direction": m["direction"],
                "channel": m["channel"],
                "provider": m["provider"],
                "shape_json": json.loads(m["shape_json"] or "[]"),
                "description": m["description"],
            }
            for m in messages
        ],
        "static_contracts": [
            {
                "direction": item["direction"], "exchange": item["channel"],
                "routing_key": item["routing_key"], "payload_type": item["payload_type"], "message_version": item["message_version"],
                "evidence": {"file": item["file_path"], "start_line": item["start_line"], "end_line": item["end_line"]},
            }
            for item in flows_repo.list_static_message_contracts(conn, row["id"])
        ],
        **page,
    }


def describe_cloud_dependencies(
    conn: sqlite3.Connection, service: str, limit: int = DEFAULT_LIST_LIMIT, offset: int = 0,
    repository: str | None = None,
) -> dict:
    error = _validate_pagination(limit, offset)
    if error:
        return {"error": error}
    row, service_error = _resolve_service(conn, service, repository)
    if service_error:
        return service_error
    static_facts, facts_page = _paginate(flows_repo.list_static_cloud_facts(conn, row["id"]), limit, offset)
    iac_resources, iac_page = _paginate(
        cloud_iac_repo.list_iac_resources_for_service(conn, row["id"]), limit, offset,
    )
    return {
        "service": row["name"], "repository": row["repository_name"],
        "static_facts": [
            {
                "provider": f["provider"], "resource_type": f["resource_type"],
                "service_name": f["service_name"], "operation": f["operation"],
                "operation_kind": f["operation_kind"], "sdk": f["sdk"], "target_name": f["target_name"],
                "evidence": {"file": f["file_path"], "start_line": f["start_line"], "end_line": f["end_line"]},
            }
            for f in static_facts
        ],
        "iac_resources": [
            {
                "provider": r["provider"], "resource_type": r["resource_type"],
                "iac_resource_type": r["iac_resource_type"], "logical_name": r["logical_name"],
                "physical_name": r["physical_name"], "source_format": r["source_format"],
                "confidence": r["confidence"], "attributes": json.loads(r["attributes_json"] or "{}"),
                "evidence": {"file": r["file_path"], "start_line": r["start_line"], "end_line": r["end_line"]},
            }
            for r in iac_resources
        ],
        "pagination": {"limit": limit, "offset": offset, "static_facts": facts_page, "iac_resources": iac_page},
    }


def search(conn: sqlite3.Connection, query: str, repository: str | None = None) -> dict:
    repository_id = None
    if repository is not None:
        repo = repositories_repo.get_repository_by_name(conn, repository)
        if repo is None:
            return {"error": f"unknown repository: {repository}"}
        repository_id = repo["id"]
    return {"results": search_repo.search(conn, query, repository_id=repository_id)}


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


def get_relationships(
    conn: sqlite3.Connection, service: str, direction: str = "both", repository: str | None = None,
) -> dict:
    """Fact + semantic-interpretation edges around one service: outbound calls it
    makes, inbound calls other services make into it, and queue/topic links inferred
    from matching publish/consume channel names."""
    row, service_error = _resolve_service(conn, service, repository)
    if service_error:
        return service_error

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

    return {"service": row["name"], "repository": row["repository_name"], "relationships": relationships}


def _outgoing_edges(conn: sqlite3.Connection, service_row: sqlite3.Row) -> list[dict]:
    edges = []
    for c in service_calls_repo.list_calls_for_service(conn, service_row["id"]):
        edges.append(
            {
                "to_service_id": c["to_service_id"],
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


def trace_flow(
    conn: sqlite3.Connection,
    from_service: str,
    to_service: str,
    max_hops: int = 6,
    from_repository: str | None = None,
    to_repository: str | None = None,
) -> dict:
    """Shortest directed path from one service to another, walking outbound calls and
    publish->consume message links — the multi-hop counterpart to get_relationships'
    single hop. Facts + semantic reasons per hop, same as get_relationships."""
    from_row, from_error = _resolve_service(conn, from_service, from_repository)
    if from_error:
        return from_error
    to_row, to_error = _resolve_service(conn, to_service, to_repository)
    if to_error:
        return to_error
    if from_row["id"] == to_row["id"]:
        return {"path": [], "reachable": True, "hops": 0, "note": "from and to are the same service"}

    visited = {from_row["id"]}
    queue = deque([(from_row, [])])
    while queue:
        current_row, path = queue.popleft()
        if len(path) >= max_hops:
            continue
        for edge in _outgoing_edges(conn, current_row):
            target_id = edge.pop("to_service_id")
            if target_id is None:
                continue
            target_row = services_repo.get_service_by_id(conn, target_id)
            if target_row is None:
                continue
            hop = {
                "from": current_row["name"], "from_repository": current_row["repository_name"],
                **edge, "to": target_row["name"], "to_repository": target_row["repository_name"],
            }
            new_path = path + [hop]
            if target_id == to_row["id"]:
                return {"path": new_path, "reachable": True, "hops": len(new_path)}
            if target_id not in visited:
                visited.add(target_id)
                queue.append((target_row, new_path))

    return {"path": [], "reachable": False, "note": f"no path found within {max_hops} hops"}


def find_architecture_smells(conn: sqlite3.Connection) -> dict:
    run_id = architecture_repo.latest_run_id(conn)
    if run_id is None:
        return {"findings": [], "run_id": None, "note": "no architecture run yet — index at least one service first"}
    findings = architecture_repo.list_findings(conn, run_id)
    def _format_finding(finding: sqlite3.Row) -> dict:
        detail = json.loads(finding["detail_json"] or "{}")
        return {
            "kind": finding["kind"], "severity": finding["severity"],
            "services": json.loads(finding["services_json"]), "detail": detail,
            "reason": finding["reason"], "confidence": detail.get("confidence", 1.0),
            "evidence": detail.get("evidence", []),
            "unknowns": detail.get("unknowns", ["Only indexed services and static facts were evaluated."]),
            "remediation": detail.get("remediation", []),
        }

    response = {
        "run_id": run_id,
        "findings": [_format_finding(finding) for finding in findings],
    }
    previous_run_id = architecture_repo.previous_run_id(conn, run_id)
    if previous_run_id is not None:
        # Omitted entirely (not an empty/null trend) on the very first run ever —
        # there's nothing honest to compare against yet, same "don't fabricate when
        # there's nothing to say" convention as find_change_surface's own fields.
        response["trend"] = diff_architecture_runs(conn, previous_run_id, run_id)
    return response


def find_change_surface(
    conn: sqlite3.Connection,
    backend: LLMBackend,
    task: str,
    hint_services: list[str] | None = None,
    repository: str | None = None,
) -> dict:
    """Task/epic -> likely change surface, computed from the already-indexed System
    Knowledge Model (no source file is read here). This is a task inference, not a
    fact: every finding carries reason + confidence + evidence."""
    repository_id = None
    if repository is not None:
        repo = repositories_repo.get_repository_by_name(conn, repository)
        if repo is None:
            return {"error": f"unknown repository: {repository}"}
        repository_id = repo["id"]
    elif duplicate_names := services_repo.list_duplicate_service_names(conn):
        return {
            "error": "ambiguous service identities; specify repository",
            "duplicate_services": duplicate_names,
        }
    response = change_surface.analyze_change_surface(
        conn, task, backend, hint_services, repository_id=repository_id,
    )
    if repository is not None:
        response["scope"] = {"repository": repository}
    return response


def plan_change(
    conn: sqlite3.Connection,
    backend: LLMBackend,
    task: str,
    hint_services: list[str] | None = None,
    repository: str | None = None,
    token_budget: int = DEFAULT_PLAN_TOKEN_BUDGET,
) -> dict:
    """Return the stable first envelope for a bounded change plan.

    This initial contract deliberately exposes only source-backed surface facts. It
    creates no target-level units or decision points until those can be derived with
    evidence rather than inferred from broad service matches.
    """
    if not 1 <= token_budget <= MAX_PLAN_TOKEN_BUDGET:
        return {
            "error": (
                f"token_budget must be between 1 and {MAX_PLAN_TOKEN_BUDGET} "
                f"(got {token_budget})"
            ),
        }
    change_surface_result = find_change_surface(conn, backend, task, hint_services, repository)
    if "error" in change_surface_result:
        return change_surface_result
    primary = change_surface_result["primary"]
    decision_points = derive_decision_points(
        change_surface_result["contracts_at_risk"], {finding["service"] for finding in primary},
    )
    status = "insufficient_evidence" if not primary else "needs_decision" if decision_points else "ready"
    repository_id = None
    if repository is not None:
        repository_id = repositories_repo.get_repository_by_name(conn, repository)["id"]
    primary_services = {finding["service"] for finding in primary}
    error_mapping_services = {
        service
        for service in primary_services
        if len(services_repo.list_service_candidates_by_name(conn, service)) == 1
    }
    if decision_points:
        change_units = []
    else:
        persistence_services = {
            item["service"]
            for item in change_surface_result["persistence_affected"]
            if item.get("service") in primary_services and item.get("kind") == "sql_table" and item.get("evidence")
        }
        migration_facts_by_service = {
            service: [dict(fact) for fact in flows_repo.list_static_migration_facts(conn, row["id"])]
            for service in persistence_services
            if (row := services_repo.get_service_by_name(conn, service, repository_id=repository_id)) is not None
        }
        feature_flags_by_service = {
            service: [dict(flag) for flag in flows_repo.list_static_feature_flags(conn, row["id"])]
            for service in primary_services
            if (row := services_repo.get_service_by_name(conn, service, repository_id=repository_id)) is not None
        }
        configuration_bindings_by_service = {
            service: [dict(binding) for binding in flows_repo.list_static_configuration_bindings(conn, row["id"])]
            for service in primary_services
            if (row := services_repo.get_service_by_name(conn, service, repository_id=repository_id)) is not None
        }
        runtime_configuration_bindings_by_service = {
            service: [
                dict(binding)
                for binding in kubernetes_configuration_repo.list_kubernetes_configuration_bindings_for_service(
                    conn, row["id"],
                )
            ]
            for service in primary_services
            if (row := services_repo.get_service_by_name(conn, service, repository_id=repository_id)) is not None
        }
        runtime_configuration_mismatches_by_service = {
            service: [
                dict(mismatch)
                for mismatch in kubernetes_configuration_repo.list_kubernetes_configuration_key_mismatches_for_service(
                    conn, row["id"],
                )
            ]
            for service in primary_services
            if (row := services_repo.get_service_by_name(conn, service, repository_id=repository_id)) is not None
        }
        runtime_configuration_source_unknowns_by_service = {
            service: [
                dict(unknown)
                for unknown in kubernetes_configuration_repo.list_kubernetes_configuration_source_unknowns_for_service(
                    conn, row["id"],
                )
            ]
            for service in primary_services
            if (row := services_repo.get_service_by_name(conn, service, repository_id=repository_id)) is not None
        }
        runtime_configuration_source_import_unknowns_by_service = {
            service: [
                dict(unknown)
                for unknown in kubernetes_configuration_repo.list_kubernetes_configuration_source_import_unknowns_for_service(
                    conn, row["id"],
                )
            ]
            for service in primary_services
            if (row := services_repo.get_service_by_name(conn, service, repository_id=repository_id)) is not None
        }
        change_units = [
            *_derive_http_contract_review_units(conn, sorted(primary_services), repository_id),
            *derive_error_mapping_review_units(find_architecture_smells(conn)["findings"], error_mapping_services),
            *derive_persistence_migration_review_units(
                change_surface_result["persistence_affected"], migration_facts_by_service, primary_services,
            ),
            *derive_feature_flag_review_units(feature_flags_by_service, primary_services),
            *derive_runtime_configuration_review_units(
                configuration_bindings_by_service, runtime_configuration_bindings_by_service, primary_services,
            ),
            *derive_runtime_configuration_mismatch_review_units(
                runtime_configuration_mismatches_by_service, primary_services,
            ),
            *derive_runtime_configuration_source_unknown_review_units(
                runtime_configuration_source_unknowns_by_service, primary_services,
            ),
            *derive_runtime_configuration_source_import_unknown_review_units(
                runtime_configuration_source_import_unknowns_by_service, primary_services,
            ),
        ]
    plan_run_id = change_plans_repo.record_plan(
        conn, change_surface_result.get("run_id"), status, token_budget, decision_points, change_units,
    )
    response = {
        "plan_id": f"cp_{plan_run_id}",
        "status": status,
        "surface": {
            "primary": primary,
            "secondary": change_surface_result["secondary"],
            "contracts_at_risk": change_surface_result["contracts_at_risk"],
        },
        "decision_points": decision_points,
        "change_units": change_units,
        "unknowns": change_surface_result["unknowns"],
        "budget": {"requested_tokens": token_budget, "estimated_tokens": 0, "truncated": False},
    }
    response["budget"]["estimated_tokens"] = (len(json.dumps(response, sort_keys=True)) + 3) // 4
    response["budget"]["truncated"] = response["budget"]["estimated_tokens"] > token_budget
    change_plans_repo.update_measurements(
        conn, plan_run_id, response["budget"]["estimated_tokens"], response["budget"]["truncated"],
    )
    return response


def _derive_http_contract_review_units(
    conn: sqlite3.Connection, primary_services: list[str], repository_id: int | None,
) -> list[dict]:
    """Return review units for fully resolved, source-proven internal HTTP calls."""
    units: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for service_name in primary_services:
        source_service = services_repo.get_service_by_name(conn, service_name, repository_id=repository_id)
        if source_service is None:
            continue
        for call in flows_repo.list_static_service_calls(conn, source_service["id"]):
            method = call["target_method"]
            path = call["target_path"]
            if call["protocol"] != "http" or not isinstance(method, str) or not isinstance(path, str):
                continue
            target_service, _candidates = services_repo.resolve_service_reference(
                conn, call["target_service"], source_service["repository_id"],
            )
            if target_service is None or flows_repo.get_entrypoint(
                conn, target_service["id"], "http", method, path,
            ) is None:
                continue
            key = service_name, target_service["name"], method, path
            if key in seen:
                continue
            seen.add(key)
            evidence = [{
                "file": call["file_path"], "start_line": call["start_line"], "end_line": call["end_line"],
            }]
            contract = f"{method} {path}"
            units.append({
                "id": f"http-contract:{service_name}:{target_service['name']}:{method}:{path}",
                "service": service_name,
                "target": {"role": "integration", "symbol": call["source"], "evidence": evidence},
                "action": "review",
                "reason": (
                    f"a source-proven HTTP call reaches {target_service['name']} {contract}; "
                    "review both sides if this boundary changes."
                ),
                "preconditions": [],
                "related_contracts": [contract],
                "dependencies": [target_service["name"]],
                "validation": [f"verify client and {target_service['name']} agree on {contract}"],
                "confidence": 1.0,
                "evidence": evidence,
            })
    return units


def refine_change_plan(conn: sqlite3.Connection, plan_id: str, decisions: list[dict]) -> dict:
    """Persist explicit decisions for an existing plan without repeating retrieval."""
    match = re.fullmatch(r"cp_([1-9][0-9]*)", plan_id)
    if match is None:
        return {"error": "invalid plan_id"}
    stored_plan = change_plans_repo.get_plan(conn, int(match.group(1)))
    if stored_plan is None:
        return {"error": f"unknown plan_id: {plan_id}"}
    decision_points = json.loads(stored_plan["decision_points_json"])
    previous_selections = json.loads(stored_plan["selected_decisions_json"])
    change_units = json.loads(stored_plan["change_units_json"])
    if previous_selections:
        if decisions != previous_selections:
            return {"error": "plan decisions already finalized"}
        return _refined_plan_response(plan_id, stored_plan["status"], previous_selections, change_units)
    selections, error = validate_decision_selections(decision_points, decisions)
    if error is not None:
        return {"error": error}
    if decision_points:
        change_units = derive_change_units(decision_points, selections)
        change_plans_repo.finalize_decisions(conn, int(match.group(1)), selections, change_units)
        return _refined_plan_response(plan_id, "ready", selections, change_units)
    return _refined_plan_response(plan_id, stored_plan["status"], selections, change_units)


def _refined_plan_response(plan_id: str, status: str, selections: list[dict], change_units: list[dict]) -> dict:
    return {
        "plan_id": plan_id,
        "status": status,
        "selected_decisions": selections,
        "remaining_decision_points": [],
        "change_units": change_units,
    }


def describe_change_unit(conn: sqlite3.Connection, plan_id: str, change_unit_id: str) -> dict:
    """Return one persisted unit and its smallest source-free reading path."""
    match = re.fullmatch(r"cp_([1-9][0-9]*)", plan_id)
    if match is None:
        return {"error": "invalid plan_id"}
    stored_plan = change_plans_repo.get_plan(conn, int(match.group(1)))
    if stored_plan is None:
        return {"error": f"unknown plan_id: {plan_id}"}
    change_unit = next(
        (unit for unit in json.loads(stored_plan["change_units_json"]) if unit.get("id") == change_unit_id),
        None,
    )
    if change_unit is None:
        return {"error": f"unknown change_unit_id: {change_unit_id}"}
    return {
        "plan_id": plan_id,
        "change_unit": change_unit,
        "minimal_reading": _minimal_unit_reading(change_unit),
        "validation": change_unit["validation"],
    }


def assess_working_change(
    conn: sqlite3.Connection, plan_id: str, repository: str, since_commit: str,
) -> dict:
    """Assess an indexed plan against one repository's Git diff without an LLM."""
    match = re.fullmatch(r"cp_([1-9][0-9]*)", plan_id)
    if match is None:
        return {"error": "plan_id must have the form cp_<positive integer>"}
    stored_plan = change_plans_repo.get_plan(conn, int(match.group(1)))
    if stored_plan is None:
        return {"error": f"unknown plan_id: {plan_id}"}
    if stored_plan["status"] != "ready":
        return {"error": f"plan must be ready before assessment (status: {stored_plan['status']})"}
    repo = repositories_repo.get_repository_by_name(conn, repository)
    if repo is None:
        return {"error": f"unknown repository: {repository}"}
    changed_files, error = git_working_changed_files_with_status(Path(repo["root_path"]), since_commit)
    if error is not None:
        return {"error": error}

    change_units = json.loads(stored_plan["change_units_json"])
    service_names = {
        service
        for unit in change_units
        for service in [unit.get("service"), *unit.get("dependencies", [])]
        if isinstance(service, str)
    }
    service_roots = {
        name: Path(service["root_path"])
        for name in service_names
        if (service := services_repo.get_service_by_name(conn, name, repository_id=repo["id"])) is not None
    }
    assessment = assess_change_units(Path(repo["root_path"]), changed_files, change_units, service_roots)
    return {"plan_id": plan_id, "repository": repository, "since_commit": since_commit, **assessment}


def _minimal_unit_reading(change_unit: dict) -> list[dict]:
    producer = change_unit["service"]
    if change_unit["target"]["role"] == "integration":
        target_service = change_unit["dependencies"][0]
        return [
            {
                "service": producer,
                "purpose": "confirm the literal outbound HTTP client",
                "recommended_query": {"tool": "describe_service", "arguments": {"service": producer}},
            },
            {
                "service": target_service,
                "purpose": "confirm the resolved target endpoint contract",
                "recommended_query": {"tool": "list_entrypoints", "arguments": {"service": target_service}},
            },
        ]
    if change_unit["target"]["role"] == "error_mapping":
        return [{
            "service": producer,
            "purpose": "identify the entrypoint that owns the public error contract",
            "recommended_query": {"tool": "list_entrypoints", "arguments": {"service": producer}},
        }]
    if change_unit["target"]["role"] == "persistence":
        return [{
            "service": producer,
            "purpose": "confirm the affected schema and indexed migration operations",
            "recommended_query": {"tool": "describe_persistence", "arguments": {"service": producer}},
        }]
    if change_unit["target"]["role"] == "feature_flag":
        return [{
            "service": producer,
            "purpose": "confirm the indexed feature flag and its guarded behavior",
            "recommended_query": {"tool": "describe_feature_flags", "arguments": {"service": producer}},
        }]
    if change_unit["target"]["role"] == "configuration":
        if change_unit["target"]["symbol"].startswith("kubernetes:"):
            is_unresolved_import = change_unit["id"].startswith("runtime-configuration-source-import-unknown:")
            purpose = (
                "confirm the owner of the unresolved Kubernetes configuration source"
                if change_unit["id"].startswith((
                    "runtime-configuration-source-unknown:",
                    "runtime-configuration-source-import-unknown:",
                ))
                else "confirm the indexed Kubernetes configuration mismatch"
            )
            if is_unresolved_import and (scope := _kubernetes_workload_reading_scope(change_unit["target"])):
                purpose = f"{purpose} and inspect {scope}"
            return [{
                "service": producer,
                "purpose": purpose,
                "recommended_query": {
                    "tool": "describe_runtime_configuration", "arguments": {"service": producer},
                },
            }]
        return [{
            "service": producer,
            "purpose": "confirm the indexed code and Kubernetes configuration binding",
            "recommended_query": {"tool": "describe_configuration", "arguments": {"service": producer}},
        }]
    reading = [{
        "service": producer,
        "purpose": "confirm the producer contract",
        "recommended_query": {"tool": "describe_messages", "arguments": {"service": producer}},
    }]
    for consumer in change_unit["dependencies"]:
        if consumer == producer:
            continue
        reading.append({
            "service": consumer,
            "purpose": "confirm consumer compatibility",
            "recommended_query": {"tool": "describe_messages", "arguments": {"service": consumer}},
        })
    return reading


def _kubernetes_workload_reading_scope(target: dict) -> str | None:
    """Format only persisted, source-proven workload scopes for a reading prompt."""
    workloads = target.get("workloads")
    if not isinstance(workloads, list):
        return None
    scopes: list[str] = []
    for workload in workloads:
        if not isinstance(workload, dict):
            continue
        kind, name, container = (workload.get(field) for field in ("kind", "name", "container"))
        if not all(isinstance(value, str) and value for value in (kind, name, container)):
            continue
        scope = f"{kind} {name} container {container}"
        if scope not in scopes:
            scopes.append(scope)
    return " and ".join(scopes) or None


def get_change_context(
    conn: sqlite3.Connection,
    backend: LLMBackend,
    task: str,
    hint_services: list[str] | None = None,
    repository: str | None = None,
    max_services: int = 3,
    epic_type: str = "unspecified",
) -> dict:
    """Return a bounded epic briefing from one change-surface inference plus facts.

    No source file is read here. The lower-level tools remain the detailed follow-up
    path; this response only selects their highest-value context for the first plan.
    """
    if not 1 <= max_services <= MAX_CONTEXT_SERVICES:
        return {"error": f"max_services must be between 1 and {MAX_CONTEXT_SERVICES} (got {max_services})"}
    if not _EPIC_TYPE.fullmatch(epic_type):
        return {"error": "epic_type must be a lowercase identifier (letters, numbers, _ or -, max 64 chars)"}
    repository_id = None
    if repository is not None:
        repo = repositories_repo.get_repository_by_name(conn, repository)
        if repo is None:
            return {"error": f"unknown repository: {repository}"}
        repository_id = repo["id"]
    surface = find_change_surface(conn, backend, task, hint_services, repository)
    if "error" in surface:
        return surface
    architecture = find_architecture_smells(conn)
    context = build_change_context(
        conn, task, surface, architecture["findings"], max_services, repository_id,
    )
    if repository is not None:
        context["scope"] = {"repository": repository}
    _record_context_telemetry(conn, context, surface, repository_id, epic_type)
    return context


def _record_context_telemetry(
    conn: sqlite3.Connection, context: dict, surface: dict, repository_id: int | None, epic_type: str,
) -> None:
    """Record calibration metadata after delivery data is ready, never blocking it.

    Task text, cards, code and recommendation reasons deliberately stay out of the
    telemetry tables. Only service IDs and bounded tool identifiers are retained.
    """
    candidates: list[dict] = []
    seen_names: set[str] = set()
    for role in ("primary", "secondary"):
        for finding in surface.get(role, []):
            name = finding["service"]
            if name in seen_names:
                continue
            seen_names.add(name)
            row = services_repo.get_service_by_name(conn, name, repository_id)
            if row is not None:
                candidates.append({"service_id": row["id"], "role": role, "rank": len(candidates) + 1})
    included = [
        row["id"]
        for card in context["services"]
        if (row := services_repo.get_service_by_name(conn, card["service"], repository_id)) is not None
    ]
    omitted = [candidate["service_id"] for candidate in candidates if candidate["service_id"] not in included]
    recommendations = []
    for rank, item in enumerate(context["recommended_next_queries"], start=1):
        arguments = item.get("arguments", {})
        service_id = None
        if service := arguments.get("service"):
            row = services_repo.get_service_by_name(conn, service, repository_id)
            service_id = row["id"] if row is not None else None
        recommendations.append({"tool": item["tool"], "service_id": service_id, "rank": rank})
    base_bytes = len(json.dumps(context, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    metadata = {
        "change_surface_run_id": surface.get("run_id"), "repository_id": repository_id, "epic_type": epic_type,
        "requested_budget": context["budget"]["max_services"], "returned_cards": context["budget"]["returned_services"],
        "candidate_count": len(candidates), "truncated": context["budget"]["truncated"],
        "response_bytes": base_bytes, "estimated_tokens": (base_bytes + 3) // 4,
        "included_service_ids": included, "omitted_service_ids": omitted,
        "candidate_ranking": candidates, "recommended_queries": recommendations,
    }
    try:
        run_id = context_telemetry_repo.record_run(conn, metadata)
        context["telemetry"] = {"recorded": True, "run_id": run_id}
        response_bytes = len(json.dumps(context, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        context_telemetry_repo.update_response_measurements(conn, run_id, response_bytes, (response_bytes + 3) // 4)
    except Exception:  # telemetry must never turn an otherwise valid context into an error
        logger.warning("context telemetry recording failed")
        context["telemetry"] = {"recorded": False}


def record_change_context_feedback(
    conn: sqlite3.Connection,
    run_id: int,
    outcome: str,
    note: str | None = None,
    missing_services: list[str] | None = None,
) -> dict:
    """Record whether a compact briefing was sufficient without retaining its note."""
    if outcome not in ("sufficient", "insufficient", "excessive"):
        return {"error": "outcome must be 'sufficient', 'insufficient' or 'excessive'"}
    run = context_telemetry_repo.get_run(conn, run_id)
    if run is None:
        return {"error": f"unknown context run_id: {run_id}"}
    missing_ids: list[int] = []
    for service in missing_services or []:
        row = services_repo.get_service_by_name(conn, service, run["repository_id"])
        if row is None:
            return {"error": f"unknown missing service: {service}"}
        missing_ids.append(row["id"])
    context_telemetry_repo.record_feedback(conn, run_id, outcome, note, missing_ids)
    return {"ok": True, "note_recorded": note is not None}


def record_context_query_execution(
    conn: sqlite3.Connection, run_id: int, tool: str, service: str | None = None,
) -> dict:
    """Associate an executed follow-up tool call with a context briefing."""
    run = context_telemetry_repo.get_run(conn, run_id)
    if run is None:
        return {"error": f"unknown context run_id: {run_id}"}
    service_id = None
    if service is not None:
        row = services_repo.get_service_by_name(conn, service, run["repository_id"])
        if row is None:
            return {"error": f"unknown service: {service}"}
        service_id = row["id"]
    recommendations = json.loads(run["recommended_queries_json"])
    if not any(item["tool"] == tool and item.get("service_id") == service_id for item in recommendations):
        return {"error": "tool/service was not recommended for this context run"}
    context_telemetry_repo.record_query_execution(conn, run_id, tool, service_id)
    return {"ok": True}


def get_context_budget_metrics(conn: sqlite3.Connection, epic_type: str | None = None) -> dict:
    """Return aggregate calibration data; no tasks, code, prompt or card text is exposed."""
    if epic_type is not None and not _EPIC_TYPE.fullmatch(epic_type):
        return {"error": "epic_type must be a lowercase identifier (letters, numbers, _ or -, max 64 chars)"}
    metrics = context_telemetry_repo.aggregate(conn, epic_type)
    feedback_total = sum(metrics["sufficiency"].values())
    metrics["recommendation"] = (
        {"status": "insufficient_history", "minimum_feedback": 3, "feedback_count": feedback_total}
        if feedback_total < 3 else
        {"status": "keep_fixed_cap", "max_services": MAX_CONTEXT_SERVICES,
         "reason": "adaptive selection is deferred until budget-specific history is evaluated"}
    )
    if epic_type is not None:
        metrics["epic_type"] = epic_type
    return metrics


def verify_context_budget(conn: sqlite3.Connection, run_id: int, repository: str, since_commit: str) -> dict:
    """Use Git ground truth to measure context-card precision, recall and omission."""
    return _verify_context_budget(conn, run_id, repository, since_commit)


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
