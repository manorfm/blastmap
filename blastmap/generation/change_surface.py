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
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from blastmap.db.repositories import apis as apis_repo
from blastmap.db.repositories import change_surface as change_surface_repo
from blastmap.db.repositories import persistence as persistence_repo
from blastmap.db.repositories import service_calls as service_calls_repo
from blastmap.db.repositories import services as services_repo
from blastmap.generation.backend_base import LLMBackend
from blastmap.generation.llm_harness import generate_with_retry, load_prompt, load_schema
from blastmap.generation.retrieval import CandidateRetrieval, KeywordGraphRetrieval

MAX_CANDIDATES = 10
MAX_LISTED_PER_SERVICE = 8
MAX_EVIDENCE_PER_SERVICE = 5


@dataclass
class ChangeSurfaceBuilder:
    """Accumulates one find_change_surface response piece by piece and emits it as
    a single immutable dict (Builder pattern) — the growth point for every new
    change-surface field (freshness, unknowns, recommended_next_queries, ...)
    instead of assembling a flat dict inline in analyze_change_surface."""

    primary: list[dict] = field(default_factory=list)
    secondary: list[dict] = field(default_factory=list)
    no_change_hint: list[dict] = field(default_factory=list)
    flow: list[dict] = field(default_factory=list)
    external_integrations: list[dict] = field(default_factory=list)
    unmapped_internal_hint: list[dict] = field(default_factory=list)
    note: str | None = None

    def with_findings(self, primary: list[dict], secondary: list[dict], no_change_hint: list[dict]) -> "ChangeSurfaceBuilder":
        self.primary = primary
        self.secondary = secondary
        self.no_change_hint = no_change_hint
        return self

    def with_flow(self, flow: list[dict]) -> "ChangeSurfaceBuilder":
        self.flow = flow
        return self

    def with_external_integrations(self, items: list[dict]) -> "ChangeSurfaceBuilder":
        self.external_integrations = items
        return self

    def with_unmapped_internal_hint(self, items: list[dict]) -> "ChangeSurfaceBuilder":
        self.unmapped_internal_hint = items
        return self

    def with_note(self, note: str) -> "ChangeSurfaceBuilder":
        self.note = note
        return self

    def build(self) -> dict:
        result = {
            "primary": self.primary,
            "secondary": self.secondary,
            "no_change_hint": self.no_change_hint,
            "flow": self.flow,
            "external_integrations": self.external_integrations,
            "unmapped_internal_hint": self.unmapped_internal_hint,
        }
        if self.note is not None:
            result["note"] = self.note
        return result


def _build_context(conn: sqlite3.Connection, candidates: list[str]) -> tuple[str, dict[str, list[dict]]]:
    blocks: list[str] = []
    evidence_by_service: dict[str, list[dict]] = {}
    for name in candidates:
        row = services_repo.get_service_by_name(conn, name)
        evidence: list[dict] = []

        apis = apis_repo.list_apis(conn, row["id"])[:MAX_LISTED_PER_SERVICE]
        api_lines = [f"  - API {a['method']} {a['path']}: {a['summary'] or ''}" for a in apis]

        outbound = service_calls_repo.list_calls_for_service(conn, row["id"])[:MAX_LISTED_PER_SERVICE]
        outbound_lines = [f"  - calls {c['to_service_name']} ({c['call_kind']}): {c['reason'] or ''}" for c in outbound]
        for c in outbound:
            evidence.extend(json.loads(c["evidence_json"] or "[]"))

        inbound = service_calls_repo.list_inbound_calls(conn, row["id"])[:MAX_LISTED_PER_SERVICE]
        inbound_lines = [f"  - called by {c['from_service_name']} ({c['call_kind']}): {c['reason'] or ''}" for c in inbound]

        persistence = persistence_repo.list_persistence(conn, row["id"])[:MAX_LISTED_PER_SERVICE]
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
    stats = change_surface_repo.get_feedback_stats(conn, service)
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
        row = services_repo.get_service_by_name(conn, name)
        if row is None:
            continue
        for c in service_calls_repo.list_calls_for_service(conn, row["id"]):
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
        row = services_repo.get_service_by_name(conn, name)
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


def analyze_change_surface(
    conn: sqlite3.Connection,
    task: str,
    backend: LLMBackend,
    hint_services: list[str] | None = None,
    max_candidates: int = MAX_CANDIDATES,
    retrieval: CandidateRetrieval | None = None,
) -> dict:
    retrieval = retrieval or KeywordGraphRetrieval()
    candidates = retrieval.candidates(conn, task, hint_services, max_candidates)
    if not candidates:
        note = "no indexed service matched this task; pass hint_services or index more of the system"
        return ChangeSurfaceBuilder().with_note(note).build()

    candidates_block, evidence_by_service = _build_context(conn, candidates)
    prompt = _render_prompt(task, candidates_block)
    schema = load_schema("change_surface")
    failures_dir = Path.home() / ".blastmap" / "failures"

    result = generate_with_retry(backend, prompt, schema, Path.home() / ".blastmap", failures_dir, "change-surface")
    if result is None:
        return ChangeSurfaceBuilder().with_note("change surface synthesis failed; see ~/.blastmap/failures").build()

    known = set(candidates)
    primary = _filter_known(conn, result.get("primary", []), known, evidence_by_service)
    secondary = _filter_known(conn, result.get("secondary", []), known, evidence_by_service)
    no_change = _filter_known(conn, result.get("no_change", []), known, evidence_by_service)

    relevant = {f["service"] for f in primary} | {f["service"] for f in secondary}
    response = (
        ChangeSurfaceBuilder()
        .with_findings(primary, secondary, no_change)
        .with_flow(_derive_flow(conn, relevant))
        .with_external_integrations(_derive_dependency_hints(conn, relevant, service_calls_repo.list_external_integration_calls))
        .with_unmapped_internal_hint(_derive_dependency_hints(conn, relevant, service_calls_repo.list_unmapped_internal_calls))
        .build()
    )
    response["run_id"] = change_surface_repo.record_change_surface_run(conn, task, backend.name, response)
    return response
