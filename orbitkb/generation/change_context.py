"""Bounded epic briefings composed from already-indexed knowledge.

This module deliberately does not perform retrieval or synthesis. It turns one
change-surface result plus compact repository facts into a token-conscious handoff,
while leaving detailed exploration to the existing MCP tools.
"""
from __future__ import annotations

import sqlite3

from orbitkb.db.repositories import apis as apis_repo
from orbitkb.db.repositories import flows as flows_repo
from orbitkb.db.repositories import messages as messages_repo
from orbitkb.db.repositories import persistence as persistence_repo
from orbitkb.db.repositories import service_calls as service_calls_repo
from orbitkb.db.repositories import services as services_repo

MAX_CONTEXT_SERVICES = 5
MAX_ITEMS_PER_SERVICE = 3


def build_change_context(
    conn: sqlite3.Connection,
    task: str,
    surface: dict,
    architecture_findings: list[dict],
    max_services: int,
    repository_id: int | None = None,
) -> dict:
    """Build an epic briefing without reading source or recalculating impact."""
    selected_names = _selected_service_names(surface, max_services)
    services = [_service_context(conn, name, repository_id) for name in selected_names]
    services = [service for service in services if service is not None]
    service_labels = {
        label
        for service in services
        for label in (service["service"], _qualified_name(service))
    }
    return {
        "task": task,
        "run_id": surface.get("run_id"),
        "scope": surface.get("scope"),
        "impact": {
            "primary": surface.get("primary", []),
            "secondary": surface.get("secondary", []),
            "flow": surface.get("flow", []),
            "external_integrations": surface.get("external_integrations", []),
            "contracts_at_risk": surface.get("contracts_at_risk", []),
            "persistence_affected": surface.get("persistence_affected", []),
        },
        "services": services,
        "architecture_risks": [
            finding for finding in architecture_findings
            if service_labels.intersection(finding["services"])
        ],
        "unknowns": surface.get("unknowns", []),
        "recommended_next_queries": surface.get("recommended_next_queries", []),
        "budget": {
            "max_services": max_services,
            "returned_services": len(services),
            "truncated": len(_all_relevant_names(surface)) > len(selected_names),
        },
        **({"note": surface["note"]} if "note" in surface else {}),
    }


def _all_relevant_names(surface: dict) -> list[str]:
    return [
        finding["service"]
        for role in ("primary", "secondary")
        for finding in surface.get(role, [])
    ]


def _selected_service_names(surface: dict, max_services: int) -> list[str]:
    return list(dict.fromkeys(_all_relevant_names(surface)))[:max_services]


def _qualified_name(service: dict) -> str:
    return f"{service['repository']}/{service['service']}" if service["repository"] else service["service"]


def _service_context(conn: sqlite3.Connection, name: str, repository_id: int | None) -> dict | None:
    row = services_repo.get_service_by_name(conn, name, repository_id)
    if row is None:
        return None
    interfaces = _interfaces(conn, row["id"])
    context = {
        "service": row["name"],
        "repository": row["repository_name"],
        "stack": row["stack"],
        "summary": row["short_desc"],
        "interfaces": interfaces[:MAX_ITEMS_PER_SERVICE],
        "outbound_dependencies": [
            {
                "service": call["to_service_name"],
                "type": call["call_kind"].upper(),
                "target_kind": call["target_kind"],
                "reason": call["reason"],
            }
            for call in service_calls_repo.list_calls_for_service(conn, row["id"])[:MAX_ITEMS_PER_SERVICE]
        ],
        "persistence": [
            {"entity": item["name"], "kind": item["kind"]}
            for item in persistence_repo.list_persistence(conn, row["id"])[:MAX_ITEMS_PER_SERVICE]
        ],
        "messages": [
            {"direction": item["direction"], "channel": item["channel"], "provider": item["provider"]}
            for item in messages_repo.list_messages(conn, row["id"])[:MAX_ITEMS_PER_SERVICE]
        ],
    }
    static_dependencies = _resolved_static_dependencies(conn, row)
    if static_dependencies:
        context["static_outbound_dependencies"] = static_dependencies
    return context


def _resolved_static_dependencies(conn: sqlite3.Connection, service: sqlite3.Row) -> list[dict]:
    """Compactly expose only unambiguous source-proven remote dependencies."""
    dependencies = []
    for call in flows_repo.list_static_service_calls(conn, service["id"]):
        target, _candidates = services_repo.resolve_service_reference(
            conn, call["target_service"], service["repository_id"],
        )
        if target is None:
            continue
        resolved_target: dict = {
            "service": target["name"], "repository": target["repository_name"],
            "status": "service_indexed",
        }
        if (
            call["protocol"] == "http"
            and isinstance(call["target_method"], str)
            and isinstance(call["target_path"], str)
        ):
            entrypoint = flows_repo.get_entrypoint(
                conn, target["id"], "http", call["target_method"], call["target_path"],
            )
            if entrypoint is not None:
                resolved_target["status"] = "endpoint_indexed"
                resolved_target["entrypoint"] = {
                    "method": entrypoint["method"], "path": entrypoint["name"],
                    "symbol": entrypoint["symbol"], "evidence": {
                        "file": entrypoint["file_path"], "start_line": entrypoint["start_line"],
                        "end_line": entrypoint["end_line"],
                    },
                }
        dependencies.append({
            "source": call["source"], "service": call["target_service"],
            "protocol": call["protocol"], "method": call["target_method"],
            "path": call["target_path"], "resolved_target": resolved_target,
        })
    return dependencies[:MAX_ITEMS_PER_SERVICE]


def _interfaces(conn: sqlite3.Connection, service_id: int) -> list[dict]:
    interfaces = [
        {"kind": "http", "method": item["method"], "name": item["path"]}
        for item in apis_repo.list_apis(conn, service_id)
    ]
    seen = {(item["kind"], item["method"], item["name"]) for item in interfaces}
    for item in flows_repo.list_entrypoints(conn, service_id):
        key = (item["kind"], item["method"], item["name"])
        if key not in seen:
            interfaces.append(dict(zip(("kind", "method", "name"), key, strict=True)))
            seen.add(key)
    return interfaces
