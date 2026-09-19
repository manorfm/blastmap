"""Mermaid diagram generation — text, not an image, so it's versionable, diffable in a
PR and renders natively in GitHub/GitLab/most editors. Generated 100% from the SQLite
index, the same way export/markdown.py is: no LLM call, no cost beyond what index/update
already paid for.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from orbitkb.db.repositories import architecture as architecture_repo
from orbitkb.db.repositories import messages as messages_repo
from orbitkb.db.repositories import persistence as persistence_repo
from orbitkb.db.repositories import service_calls as service_calls_repo
from orbitkb.db.repositories import services as services_repo


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower() or "node"


def _sanitize_ident(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", text or "").strip("_") or "field"


def _cycle_service_names(conn: sqlite3.Connection) -> set[str]:
    run_id = architecture_repo.latest_run_id(conn)
    if run_id is None:
        return set()
    names: set[str] = set()
    for finding in architecture_repo.list_findings(conn, run_id):
        if finding["kind"] == "cycle":
            names.update(json.loads(finding["services_json"]))
    return names


def generate_topology_diagram(conn: sqlite3.Connection) -> str:
    """System-wide `graph TD`: every indexed service as a node, external vendors as
    rounded nodes, service_calls as solid edges, message links as dashed edges.
    Services involved in a cycle (find_architecture_smells) are styled distinctly —
    the one piece of interpretation on top of otherwise purely structural facts."""
    cycle_names = _cycle_service_names(conn)
    lines = ["graph TD"]

    service_ids: dict[str, str] = {}
    for svc in services_repo.list_services(conn):
        node_id = f"svc_{_slug(svc['name'])}"
        service_ids[svc["name"]] = node_id
        lines.append(f'  {node_id}["{svc["name"]}"]')

    external_ids: dict[str, str] = {}

    def external_node(name: str) -> str:
        if name not in external_ids:
            node_id = f"ext_{_slug(name)}"
            external_ids[name] = node_id
            lines.append(f'  {node_id}(("{name}"))')
        return external_ids[name]

    for edge in service_calls_repo.list_internal_edges(conn):
        lines.append(f'  {service_ids[edge["from_name"]]} -->|{edge["call_kind"]}| {service_ids[edge["to_name"]]}')

    for edge in service_calls_repo.list_external_edges(conn):
        from_id = service_ids.get(edge["from_name"])
        if from_id is None:
            continue
        target_id = external_node(edge["to_service_name"])
        label = edge["resource_type"] or "external"
        lines.append(f"  {from_id} -.->|{label}| {target_id}")

    for link in messages_repo.list_all_message_links(conn):
        publisher_id = service_ids.get(link["publisher"])
        consumer_id = service_ids.get(link["consumer"])
        if publisher_id is None or consumer_id is None:
            continue
        lines.append(f'  {publisher_id} ==>|{link["channel"]}| {consumer_id}')

    if cycle_names:
        lines.append("  classDef cycle fill:#f88,stroke:#900,stroke-width:2px;")
        cycle_node_ids = ",".join(service_ids[name] for name in sorted(cycle_names) if name in service_ids)
        if cycle_node_ids:
            lines.append(f"  class {cycle_node_ids} cycle;")

    return "\n".join(lines)


def generate_er_diagram(conn: sqlite3.Connection, service_name: str) -> str | None:
    """One service's `erDiagram`: an entity block per persisted table/collection, with
    its fields. No relationship lines between entities — the index doesn't track
    foreign keys yet (see the project's documented gaps), so this never fabricates one;
    it's a field-level reference, not a full ER diagram in the classic sense."""
    row = services_repo.get_service_by_name(conn, service_name)
    if row is None:
        return None
    entities = persistence_repo.list_persistence(conn, row["id"])
    lines = [
        "erDiagram",
        "  %% No cross-entity relationships shown: the index doesn't track foreign keys yet.",
    ]
    for entity in entities:
        entity_id = _sanitize_ident(entity["name"])
        lines.append(f"  {entity_id} {{")
        for field in json.loads(entity["schema_json"] or "[]"):
            type_token = _sanitize_ident((field.get("type_desc") or "string").split(",")[0].strip()) or "string"
            field_name = _sanitize_ident(field.get("field", "field"))
            lines.append(f"    {type_token} {field_name}")
        lines.append("  }")
    return "\n".join(lines)


def export_mermaid(conn: sqlite3.Connection, out_dir: Path, service_filter: str | None = None) -> list[Path]:
    """Writes `<out>/topology.mmd` (always) and `<out>/<service>/er.mmd` for every
    service that persists at least one entity — mirrors export/markdown.py's
    per-service directory layout."""
    written: list[Path] = []
    out_dir.mkdir(parents=True, exist_ok=True)

    if service_filter is None:
        topology_path = out_dir / "topology.mmd"
        topology_path.write_text(generate_topology_diagram(conn) + "\n", encoding="utf-8")
        written.append(topology_path)

    for svc in services_repo.list_services(conn):
        if service_filter and svc["name"] != service_filter:
            continue
        diagram = generate_er_diagram(conn, svc["name"])
        if not diagram or diagram.count("\n") <= 1:  # header lines only, no entities
            continue
        service_dir = out_dir / svc["name"]
        service_dir.mkdir(parents=True, exist_ok=True)
        er_path = service_dir / "er.mmd"
        er_path.write_text(diagram + "\n", encoding="utf-8")
        written.append(er_path)

    return written
