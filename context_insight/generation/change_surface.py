"""Task/epic -> Change Surface: maps a free-text task to the services that likely
need to change, using only the already-indexed System Knowledge Model (no re-reading
of source code). Reuses the same LLMBackend + prompt/schema + retry harness as the
indexing pipeline (see orchestrator.py) for the one synthesis call it makes.

Output is explicitly a task inference, not a fact: every primary/secondary finding
carries a reason, a confidence, and the evidence it was grounded in, and the LLM is
constrained to a closed candidate list gathered by SQL beforehand — any service name
it returns outside that list is dropped rather than trusted (see _filter_known).
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from context_insight.db import repository
from context_insight.generation.backend_base import LLMBackend
from context_insight.generation.llm_harness import generate_with_retry, load_prompt, load_schema

MAX_CANDIDATES = 10
MAX_LISTED_PER_SERVICE = 8
MAX_EVIDENCE_PER_SERVICE = 5

_STOPWORDS = {
    "the", "a", "an", "to", "for", "in", "on", "of", "and", "or", "with", "add", "support",
    "de", "da", "do", "das", "dos", "em", "no", "na", "para", "com", "que", "um", "uma", "e",
}


def _extract_keywords(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    seen: list[str] = []
    for w in words:
        if len(w) >= 3 and w not in _STOPWORDS and w not in seen:
            seen.append(w)
    return seen


def _seed_services(conn: sqlite3.Connection, task: str) -> set[str]:
    seeds: set[str] = set()
    for keyword in _extract_keywords(task):
        for r in repository.search(conn, keyword, limit=20):
            seeds.add(r["service"])
    return seeds


def _expand_candidates(conn: sqlite3.Connection, seeds: set[str], max_candidates: int, hops: int = 2) -> list[str]:
    """Breadth-first walk of the relationship graph (get_relationships' own edges,
    reused directly), starting from the keyword-matched seeds. Bounded to a couple of
    hops and max_candidates so a service connected only through an intermediate one
    (e.g. order-service <- payments-service <- checkout-service) still surfaces,
    without turning into an unbounded system-wide traversal. Still pure SQL — no
    source file is read to compute this.
    """
    visited: set[str] = set()
    frontier: set[str] = set(seeds)
    for _ in range(hops):
        if not frontier or len(visited) >= max_candidates:
            break
        newly_found: set[str] = set()
        for name in frontier:
            visited.add(name)
            row = repository.get_service_by_name(conn, name)
            if row is None:
                continue
            for c in repository.list_calls_for_service(conn, row["id"]):
                newly_found.add(c["to_service_name"])
            for c in repository.list_inbound_calls(conn, row["id"]):
                newly_found.add(c["from_service_name"])
            for link in repository.list_message_links(conn, row["id"]):
                newly_found.add(link["other_service"])
        frontier = newly_found - visited
    visited |= frontier  # include the last frontier even though its own edges go unexplored

    # Only keep names that resolve to a real indexed service — a dangling
    # to_service_name with no matching row can't be given any context below.
    known = [name for name in visited if repository.get_service_by_name(conn, name) is not None]
    return sorted(known)[:max_candidates]


def _build_context(conn: sqlite3.Connection, candidates: list[str]) -> tuple[str, dict[str, list[dict]]]:
    blocks: list[str] = []
    evidence_by_service: dict[str, list[dict]] = {}
    for name in candidates:
        row = repository.get_service_by_name(conn, name)
        evidence: list[dict] = []

        apis = repository.list_apis(conn, row["id"])[:MAX_LISTED_PER_SERVICE]
        api_lines = [f"  - API {a['method']} {a['path']}: {a['summary'] or ''}" for a in apis]

        outbound = repository.list_calls_for_service(conn, row["id"])[:MAX_LISTED_PER_SERVICE]
        outbound_lines = [f"  - calls {c['to_service_name']} ({c['call_kind']}): {c['reason'] or ''}" for c in outbound]
        for c in outbound:
            evidence.extend(json.loads(c["evidence_json"] or "[]"))

        inbound = repository.list_inbound_calls(conn, row["id"])[:MAX_LISTED_PER_SERVICE]
        inbound_lines = [f"  - called by {c['from_service_name']} ({c['call_kind']}): {c['reason'] or ''}" for c in inbound]

        persistence = repository.list_persistence(conn, row["id"])[:MAX_LISTED_PER_SERVICE]
        persistence_lines = [f"  - persists {p['name']} ({p['kind']})" for p in persistence]
        for p in persistence:
            evidence.extend(json.loads(p["evidence_json"] or "[]"))

        for a in apis:
            evidence.extend(json.loads(a["evidence_json"] or "[]"))

        evidence_by_service[name] = evidence[:MAX_EVIDENCE_PER_SERVICE]

        lines = [f"- {name}: {row['short_desc'] or '(no description indexed)'}"]
        lines += api_lines or ["  - (no APIs indexed)"]
        lines += outbound_lines
        lines += inbound_lines
        lines += persistence_lines
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks), evidence_by_service


def _render_prompt(task: str, candidates_block: str) -> str:
    return load_prompt("change_surface").substitute(task=task, candidates=candidates_block)


MIN_FEEDBACK_SAMPLES = 3
# How much historical accuracy for this exact service nudges a fresh LLM confidence.
# Kept low deliberately: the LLM saw real evidence for this specific task, while
# feedback history is a coarser, task-agnostic signal — it should nudge, not override.
FEEDBACK_BLEND_WEIGHT = 0.3


def _recalibrate_confidence(conn: sqlite3.Connection, service: str, confidence: float) -> float:
    stats = repository.get_feedback_stats(conn, service)
    total = stats["confirmed"] + stats["rejected"]
    if total < MIN_FEEDBACK_SAMPLES:
        return confidence
    precision = stats["confirmed"] / total
    blended = (1 - FEEDBACK_BLEND_WEIGHT) * confidence + FEEDBACK_BLEND_WEIGHT * precision
    return round(max(0.0, min(1.0, blended)), 4)


def _filter_known(
    conn: sqlite3.Connection, findings: list[dict], known: set[str], evidence_by_service: dict[str, list[dict]]
) -> list[dict]:
    out = []
    for f in findings:
        name = f.get("service")
        if name not in known:
            continue  # drop any service the LLM invented outside the given candidate list
        confidence = max(0.0, min(1.0, float(f.get("confidence", 0) or 0)))
        confidence = _recalibrate_confidence(conn, name, confidence)
        out.append(
            {
                "service": name,
                "reason": f.get("reason", ""),
                "confidence": confidence,
                "evidence": evidence_by_service.get(name, []),
            }
        )
    return out


def _derive_flow(conn: sqlite3.Connection, service_names: set[str]) -> list[dict]:
    flow = []
    for name in service_names:
        row = repository.get_service_by_name(conn, name)
        if row is None:
            continue
        for c in repository.list_calls_for_service(conn, row["id"]):
            if c["to_service_name"] in service_names:
                flow.append({"from": name, "to": c["to_service_name"], "type": c["call_kind"].upper()})
    return flow


def _derive_dependency_hints(conn: sqlite3.Connection, service_names: set[str], lister) -> list[dict]:
    """Shared shape for external_integrations/unmapped_internal_hint: both are just
    'this relevant service's outbound calls of one target_kind', attributed back to
    which service they came from. Computed purely from already-classified
    service_calls rows — no extra LLM cost, no re-reading source.
    """
    out: list[dict] = []
    for name in sorted(service_names):
        row = repository.get_service_by_name(conn, name)
        if row is None:
            continue
        for c in lister(conn, row["id"]):
            out.append(
                {
                    "service": c["to_service_name"],
                    "via_service": name,
                    "reason": c["reason"],
                    "confidence": c["confidence"],
                    "evidence": json.loads(c["evidence_json"] or "[]"),
                }
            )
    return out


def _empty_result(note: str) -> dict:
    return {
        "primary": [], "secondary": [], "no_change_hint": [], "flow": [],
        "external_integrations": [], "unmapped_internal_hint": [], "note": note,
    }


def analyze_change_surface(
    conn: sqlite3.Connection,
    task: str,
    backend: LLMBackend,
    hint_services: list[str] | None = None,
    max_candidates: int = MAX_CANDIDATES,
) -> dict:
    seeds = _seed_services(conn, task)
    if hint_services:
        seeds |= set(hint_services)

    if not seeds:
        return _empty_result("no indexed service matched this task; pass hint_services or index more of the system")

    candidates = _expand_candidates(conn, seeds, max_candidates)
    if not candidates:
        return _empty_result("matched services could not be resolved to indexed rows")

    candidates_block, evidence_by_service = _build_context(conn, candidates)
    prompt = _render_prompt(task, candidates_block)
    schema = load_schema("change_surface")
    failures_dir = Path.home() / ".context_insight" / "failures"

    result = generate_with_retry(backend, prompt, schema, Path.home() / ".context_insight", failures_dir, "change-surface")
    if result is None:
        return _empty_result("change surface synthesis failed; see ~/.context_insight/failures")

    known = set(candidates)
    primary = _filter_known(conn, result.get("primary", []), known, evidence_by_service)
    secondary = _filter_known(conn, result.get("secondary", []), known, evidence_by_service)
    no_change = _filter_known(conn, result.get("no_change", []), known, evidence_by_service)

    relevant = {f["service"] for f in primary} | {f["service"] for f in secondary}
    flow = _derive_flow(conn, relevant)
    external_integrations = _derive_dependency_hints(conn, relevant, repository.list_external_integration_calls)
    unmapped_internal_hint = _derive_dependency_hints(conn, relevant, repository.list_unmapped_internal_calls)

    response = {
        "primary": primary,
        "secondary": secondary,
        "no_change_hint": no_change,
        "flow": flow,
        "external_integrations": external_integrations,
        "unmapped_internal_hint": unmapped_internal_hint,
    }
    response["run_id"] = repository.record_change_surface_run(conn, task, backend.name, response)
    return response
