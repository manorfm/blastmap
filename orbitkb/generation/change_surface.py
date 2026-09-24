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

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from orbitkb.db.repositories import apis as apis_repo
from orbitkb.db.repositories import change_surface as change_surface_repo
from orbitkb.db.repositories import embeddings as embeddings_repo
from orbitkb.db.repositories import flows as flows_repo
from orbitkb.db.repositories import messages as messages_repo
from orbitkb.db.repositories import persistence as persistence_repo
from orbitkb.db.repositories import service_calls as service_calls_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.db.repositories import verification as verification_repo
from orbitkb.generation import embeddings
from orbitkb.generation.backend_base import LLMBackend, LLMUsage
from orbitkb.generation.embeddings import EmbeddingBackend, cosine_similarity
from orbitkb.generation.freshness import compute_freshness
from orbitkb.generation.llm_harness import generate_with_retry, load_prompt, load_schema
from orbitkb.generation.next_queries import NextQueryRecommender
from orbitkb.generation.retrieval import (
    CandidateRetrieval,
    FallbackRetrieval,
    KeywordGraphRetrieval,
    SemanticRetrieval,
)

MAX_CANDIDATES = 10
MAX_LISTED_PER_SERVICE = 8
MAX_EVIDENCE_PER_SERVICE = 5
MAX_STATIC_DEPENDENCY_FINDINGS = 3


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
    contracts_at_risk: list[dict] = field(default_factory=list)
    persistence_affected: list[dict] = field(default_factory=list)
    freshness: dict[str, dict] = field(default_factory=dict)
    unknowns: list[dict] = field(default_factory=list)
    recommended_next_queries: list[dict] = field(default_factory=list)
    similar_past_tasks: list[dict] = field(default_factory=list)
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

    def with_contracts_at_risk(self, items: list[dict]) -> "ChangeSurfaceBuilder":
        self.contracts_at_risk = items
        return self

    def with_persistence_affected(self, items: list[dict]) -> "ChangeSurfaceBuilder":
        self.persistence_affected = items
        return self

    def with_freshness(self, freshness: dict[str, dict]) -> "ChangeSurfaceBuilder":
        self.freshness = freshness
        return self

    def with_unknowns(self, items: list[dict]) -> "ChangeSurfaceBuilder":
        self.unknowns = items
        return self

    def with_recommended_next_queries(self, items: list[dict]) -> "ChangeSurfaceBuilder":
        self.recommended_next_queries = items
        return self

    def with_similar_past_tasks(self, items: list[dict]) -> "ChangeSurfaceBuilder":
        self.similar_past_tasks = items
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
            "contracts_at_risk": self.contracts_at_risk,
            "persistence_affected": self.persistence_affected,
            "freshness": self.freshness,
            "unknowns": self.unknowns,
            "recommended_next_queries": self.recommended_next_queries,
            "similar_past_tasks": self.similar_past_tasks,
        }
        if self.note is not None:
            result["note"] = self.note
        return result


def _build_context(
    conn: sqlite3.Connection, candidates: list[str], repository_id: int | None = None,
) -> tuple[str, dict[str, list[dict]]]:
    blocks: list[str] = []
    evidence_by_service: dict[str, list[dict]] = {}
    for name in candidates:
        row = services_repo.get_service_by_name(conn, name, repository_id=repository_id)
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


def _synthesis_cache_key(backend_name: str, prompt: str, schema: dict, repository_id: int | None) -> str:
    """Fingerprint every input that could alter the bounded LLM synthesis."""
    digest = hashlib.sha256()
    for value in (backend_name, prompt, json.dumps(schema, sort_keys=True, separators=(",", ":")), str(repository_id)):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


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


def _derive_flow(conn: sqlite3.Connection, service_names: set[str], repository_id: int | None = None) -> list[dict]:
    flow_by_relation: dict[tuple[str, str, str], dict] = {}
    for name in service_names:
        row = services_repo.get_service_by_name(conn, name, repository_id=repository_id)
        if row is None:
            continue
        for c in service_calls_repo.list_calls_for_service(conn, row["id"]):
            if c["to_service_name"] in service_names:
                relation = (name, c["to_service_name"], c["call_kind"].upper())
                flow_by_relation[relation] = {"from": name, "to": c["to_service_name"], "type": relation[2]}
        for call in flows_repo.list_static_service_calls(conn, row["id"]):
            target, _candidates = services_repo.resolve_service_reference(
                conn, call["target_service"], row["repository_id"],
            )
            if (
                target is None
                or target["name"] not in service_names
                or (repository_id is not None and target["repository_id"] != repository_id)
            ):
                continue
            relation = (name, target["name"], call["protocol"].upper())
            flow_by_relation[relation] = {
                "from": name, "to": target["name"], "type": relation[2], "origin": "static",
            }
    return list(flow_by_relation.values())


def _derive_static_dependency_findings(
    conn: sqlite3.Connection,
    primary_names: list[str],
    excluded_names: set[str],
    repository_id: int | None = None,
) -> list[dict]:
    """Add direct, source-proven dependencies of primary services as review targets.

    The call itself is deterministic, while whether a requested change crosses its
    client boundary remains an inference. It is therefore a bounded secondary
    finding with explicit provenance, never promoted to a primary edit mandate.
    """
    findings = []
    seen_targets: set[int] = set()
    for source_name in primary_names:
        source = services_repo.get_service_by_name(conn, source_name, repository_id=repository_id)
        if source is None:
            continue
        for call in flows_repo.list_static_service_calls(conn, source["id"]):
            target, _candidates = services_repo.resolve_service_reference(
                conn, call["target_service"], source["repository_id"],
            )
            if (
                target is None
                or target["id"] in seen_targets
                or target["name"] in excluded_names
                or (repository_id is not None and target["repository_id"] != repository_id)
            ):
                continue
            seen_targets.add(target["id"])
            method = call["target_method"] or "UNKNOWN"
            path = call["target_path"] or "UNKNOWN"
            findings.append({
                "service": target["name"],
                "reason": (
                    f"{source_name} has a source-proven {call['protocol'].upper()} call to "
                    f"{target['name']} {method} {path}; review the client boundary and remote contract."
                ),
                "confidence": 0.6,
                "origin": "static_dependency",
                "via_service": source_name,
                "evidence": [{
                    "file": call["file_path"], "start_line": call["start_line"], "end_line": call["end_line"],
                }],
            })
            if len(findings) >= MAX_STATIC_DEPENDENCY_FINDINGS:
                return findings
    return findings


def _derive_dependency_hints(
    conn: sqlite3.Connection, service_names: set[str], lister, repository_id: int | None = None,
) -> list[dict]:
    """Shared shape for external_integrations/unmapped_internal_hint: both are just
    'this relevant service's outbound calls of one target_kind', attributed back to
    which service they came from. Computed purely from already-classified
    service_calls rows — no extra LLM cost, no re-reading source.
    """
    out: list[dict] = []
    for name in sorted(service_names):
        row = services_repo.get_service_by_name(conn, name, repository_id=repository_id)
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


def _derive_cloud_dependency_hints(
    conn: sqlite3.Connection, service_names: set[str], repository_id: int | None = None,
) -> list[dict]:
    """The deterministic counterpart to _derive_dependency_hints' service_calls-
    sourced external_integrations: a relevant service's own proven cloud SDK call
    sites (see describe_cloud_dependencies), reshaped into the same generic
    {service, via_service, reason, confidence, evidence} shape so an agent reading
    external_integrations sees both kinds of dependency without needing to know
    they come from two different tables."""
    out: list[dict] = []
    for name in sorted(service_names):
        row = services_repo.get_service_by_name(conn, name, repository_id=repository_id)
        if row is None:
            continue
        for fact in flows_repo.list_static_cloud_facts(conn, row["id"]):
            out.append({
                "service": f"{fact['provider']}:{fact['service_name']}",
                "via_service": name,
                "reason": f"static {fact['operation']} ({fact['operation_kind']})",
                "confidence": 1.0,
                "evidence": [{
                    "file": fact["file_path"], "start_line": fact["start_line"], "end_line": fact["end_line"],
                }],
            })
    return out


def _compute_freshness_for(
    conn: sqlite3.Connection, service_names: set[str], repository_id: int | None = None,
) -> dict[str, dict]:
    freshness: dict[str, dict] = {}
    for name in service_names:
        row = services_repo.get_service_by_name(conn, name, repository_id=repository_id)
        if row is None:
            continue
        freshness[name] = compute_freshness(row["updated_at"], row["last_commit"], row["root_path"])
    return freshness


def _derive_contracts_at_risk(
    conn: sqlite3.Connection, service_names: set[str], repository_id: int | None = None,
) -> list[dict]:
    """Events published by a relevant service, and every other indexed service that
    consumes them — reuses the same publish<->consume channel-name join as
    get_relationships/trace_flow (messages_repo.list_message_links), just grouped by
    channel instead of walked edge by edge. Always phrased as risk, never as a
    confirmed break: a consumer is 'potentially affected', not broken.
    """
    contracts: dict[str, dict] = {}
    for name in sorted(service_names):
        row = services_repo.get_service_by_name(conn, name, repository_id=repository_id)
        if row is None:
            continue
        published = {m["channel"]: m for m in messages_repo.list_messages(conn, row["id"]) if m["direction"] == "publishes"}
        if not published:
            continue
        for link in messages_repo.list_message_links(conn, row["id"]):
            if link["local_direction"] != "publishes" or link["channel"] not in published:
                continue
            entry = contracts.setdefault(
                link["channel"],
                {
                    "contract": link["channel"],
                    "producer": name,
                    "consumers": [],
                    "reason": "potentially affects its consumers; requires verification",
                    "evidence": json.loads(published[link["channel"]]["evidence_json"] or "[]"),
                },
            )
            if link["other_service"] not in entry["consumers"]:
                entry["consumers"].append(link["other_service"])
    return list(contracts.values())


def _derive_persistence_for(
    conn: sqlite3.Connection, service_names: set[str], repository_id: int | None = None,
) -> list[dict]:
    """What each relevant service persists — a direct read of persistence_entities,
    no LLM cost. Tells an agent what storage a change might also need to touch
    without a separate describe_persistence round trip for the obvious cases.
    """
    out: list[dict] = []
    for name in sorted(service_names):
        row = services_repo.get_service_by_name(conn, name, repository_id=repository_id)
        if row is None:
            continue
        for p in persistence_repo.list_persistence(conn, row["id"]):
            out.append(
                {
                    "service": name,
                    "entity": p["name"],
                    "kind": p["kind"],
                    "evidence": json.loads(p["evidence_json"] or "[]"),
                }
            )
    return out


def _derive_stale_unknowns(freshness: dict[str, dict]) -> list[dict]:
    """Freshness is per-service; this is what makes it actionable at the change-
    surface level: a stale relevant service means its stored dependency reasons
    (why it calls what, with what data) may themselves be out of date, so an agent
    shouldn't just trust them silently.
    """
    return [
        {
            "status": "unknown",
            "service": name,
            "reason": f"indexed knowledge for {name} may be stale (new commits since indexing)",
            "suggestion": f"reindex {name} before trusting its dependency reasons",
        }
        for name, f in freshness.items()
        if f.get("stale") is True
    ]


def _derive_unknowns_from_unmapped(unmapped_internal_hint: list[dict]) -> list[dict]:
    """Every unmapped_internal_hint finding is, by definition, a gap in the System
    Knowledge Model: a dependency that looks internal but was never indexed. Restate
    each one as an explicit unknowns entry so an agent can branch on `status ==
    "unknown"` directly instead of having to infer that meaning from the bucket name.
    """
    return [
        {
            "status": "unknown",
            "service": hint["service"],
            "reason": "looks internal but has not been indexed yet",
            "suggestion": "index this repository for a fuller picture",
        }
        for hint in unmapped_internal_hint
    ]


def _default_retrieval(embedding_backend: EmbeddingBackend | None) -> CandidateRetrieval:
    """Keyword retrieval is always free and stays the primary strategy; semantic
    retrieval (local embeddings, see generation/embeddings.py) is only added as a
    FallbackRetrieval secondary when the optional `semantic` extra is installed —
    a query whose vocabulary already matches something indexed never pays the extra
    encode cost."""
    if embedding_backend is None:
        return KeywordGraphRetrieval()
    return FallbackRetrieval(KeywordGraphRetrieval(), SemanticRetrieval(embedding_backend))


def _describe_outcome(conn: sqlite3.Connection, run_id: int) -> str:
    """Honest summary of what happened after a past run, in priority order: a real
    git-verified precision/recall (verify_change_surface) beats self-reported
    feedback (record_change_surface_feedback), which beats nothing at all — never
    fabricated when there's genuinely no signal yet."""
    verifications = verification_repo.list_verifications_for_run(conn, run_id)
    if verifications:
        latest = verifications[0]
        return f"verified precision={latest['precision']} recall={latest['recall']}"
    feedback = change_surface_repo.list_feedback_for_run(conn, run_id)
    if feedback:
        confirmed = sum(1 for f in feedback if f["outcome"] == "confirmed")
        rejected = sum(1 for f in feedback if f["outcome"] == "rejected")
        return f"feedback: {confirmed} confirmed, {rejected} rejected"
    return "no feedback yet"


def _find_similar_past_tasks(conn: sqlite3.Connection, task_vector: list[float], top_k: int = 3) -> list[dict]:
    """Historical precedent: past find_change_surface runs ranked by cosine
    similarity of their task text to this one, each restated with what it predicted
    and — when known — what actually happened (see _describe_outcome). Zero extra
    LLM cost: reuses the same local embedding already computed for this run. Called
    before this run's own embedding is inserted, so there's nothing to exclude.
    """
    rows = embeddings_repo.get_all_change_surface_run_embeddings(conn)
    scored = sorted(
        ((cosine_similarity(task_vector, json.loads(row["vector_json"])), row["run_id"]) for row in rows),
        key=lambda item: item[0], reverse=True,
    )
    results = []
    for score, run_id in scored[:top_k]:
        run = change_surface_repo.get_change_surface_run(conn, run_id)
        if run is None:
            continue
        findings = change_surface_repo.list_change_surface_findings(conn, run_id)
        primary_services = [f["service"] for f in findings if f["role"] == "primary"]
        results.append(
            {
                "run_id": run_id,
                "task": run["task_text"],
                "similarity": round(score, 4),
                "primary_services": primary_services,
                "outcome": _describe_outcome(conn, run_id),
            }
        )
    return results


def analyze_change_surface(
    conn: sqlite3.Connection,
    task: str,
    backend: LLMBackend,
    hint_services: list[str] | None = None,
    max_candidates: int = MAX_CANDIDATES,
    retrieval: CandidateRetrieval | None = None,
    repository_id: int | None = None,
) -> dict:
    embedding_backend = embeddings.try_create_default_backend()
    retrieval = retrieval or _default_retrieval(embedding_backend)
    candidates = retrieval.candidates(conn, task, hint_services, max_candidates, repository_id)
    if not candidates:
        note = "no indexed service matched this task; pass hint_services or index more of the system"
        suggestion = "pass hint_services or index more of the system"
        unknown = {"status": "unknown", "reason": "no indexed service matched this task", "suggestion": suggestion}
        return ChangeSurfaceBuilder().with_note(note).with_unknowns([unknown]).build()

    candidates_block, evidence_by_service = _build_context(conn, candidates, repository_id)
    prompt = _render_prompt(task, candidates_block)
    schema = load_schema("change_surface")
    failures_dir = Path.home() / ".orbitkb" / "failures"
    cache_key = _synthesis_cache_key(backend.name, prompt, schema, repository_id)
    result = change_surface_repo.get_synthesis_cache(conn, cache_key, backend.name)
    cache_hit = result is not None
    usage = LLMUsage(input_tokens=0, output_tokens=0, cost_usd=0) if cache_hit else None
    if result is None:
        generation = generate_with_retry(backend, prompt, schema, Path.home() / ".orbitkb", failures_dir, "change-surface")
        if generation is None:
            return ChangeSurfaceBuilder().with_note("change surface synthesis failed; see ~/.orbitkb/failures").build()
        result = generation.structured
        usage = generation.usage
        change_surface_repo.put_synthesis_cache(conn, cache_key, backend.name, result)

    known = set(candidates)
    primary = _filter_known(conn, result.get("primary", []), known, evidence_by_service)
    secondary = _filter_known(conn, result.get("secondary", []), known, evidence_by_service)
    no_change = _filter_known(conn, result.get("no_change", []), known, evidence_by_service)

    primary_names = [f["service"] for f in primary]
    excluded_static_targets = {
        finding["service"] for finding in [*primary, *secondary, *no_change]
    }
    secondary.extend(
        _derive_static_dependency_findings(conn, primary_names, excluded_static_targets, repository_id)
    )
    secondary_names = [f["service"] for f in secondary]
    relevant = set(primary_names) | set(secondary_names)
    unmapped_internal_hint = _derive_dependency_hints(
        conn, relevant, service_calls_repo.list_unmapped_internal_calls, repository_id,
    )
    next_queries = NextQueryRecommender().recommend(
        conn, primary_names, secondary_names, unmapped_internal_hint, repository_id,
    )
    freshness = _compute_freshness_for(conn, relevant, repository_id)
    unknowns = _derive_unknowns_from_unmapped(unmapped_internal_hint) + _derive_stale_unknowns(freshness)

    # Computed once here (not inside the Builder) so the SAME vector is both looked
    # up against past runs and, further below, persisted as this run's own — never
    # re-embedded twice for two different purposes.
    task_vector = embedding_backend.embed([task])[0] if embedding_backend is not None else None
    similar_past_tasks = _find_similar_past_tasks(conn, task_vector) if task_vector is not None else []

    response = (
        ChangeSurfaceBuilder()
        .with_findings(primary, secondary, no_change)
        .with_flow(_derive_flow(conn, relevant, repository_id))
        .with_external_integrations(
            _derive_dependency_hints(
                conn, relevant, service_calls_repo.list_external_integration_calls, repository_id,
            )
            + _derive_cloud_dependency_hints(conn, relevant, repository_id)
        )
        .with_unmapped_internal_hint(unmapped_internal_hint)
        .with_contracts_at_risk(_derive_contracts_at_risk(conn, relevant, repository_id))
        .with_persistence_affected(_derive_persistence_for(conn, relevant, repository_id))
        .with_freshness(freshness)
        .with_unknowns(unknowns)
        .with_recommended_next_queries(next_queries)
        .with_similar_past_tasks(similar_past_tasks)
        .build()
    )
    response["synthesis_cache"] = {"hit": cache_hit}
    response["run_id"] = change_surface_repo.record_change_surface_run(
        conn, task, backend.name, response,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=usage.cost_usd,
    )
    response["run_cost_usd"] = usage.cost_usd
    if task_vector is not None:
        embeddings_repo.upsert_change_surface_run_embedding(
            conn, response["run_id"], embedding_backend.model_name, task_vector
        )
    return response
