from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from impactmesh.config import resolve_backend
from impactmesh.db.connection import open_db
from impactmesh.generation.backend_base import LLMBackend
from impactmesh.mcp import queries


def build_server(db_path: Path | None = None, backend: LLMBackend | None = None) -> MCPServer:
    mcp = MCPServer("impactmesh")
    # Only find_change_surface uses a backend; the other 13 tools are pure SQLite
    # reads and never touch it. Resolved once here rather than per-call since
    # constructing a backend is cheap (no subprocess runs until .generate() is called).
    resolved_backend = backend or resolve_backend(None)

    def _conn() -> sqlite3.Connection:
        # A fresh short-lived connection per call: tool handlers may run on different
        # worker threads, and sqlite3 connections aren't safe to share across threads.
        return open_db(db_path)

    @mcp.tool()
    def list_repositories() -> dict:
        """Call this to see what's been cumulatively indexed so far, at the
        repository level — name, root path and how many services came from it.
        Knowledge here is additive: repositories can be indexed one at a time or as
        one monorepo, and this reflects everything indexed up to now, not just the
        last run. Next: list_services for the service-level view, or
        find_change_surface if you already have a specific engineering task."""
        with closing(_conn()) as conn:
            return queries.list_repositories(conn)

    @mcp.tool()
    def list_services() -> dict:
        """Call this first, to see what's indexed: every microservice with a
        one-line description, stack and API count. Next: describe_service on
        whichever one is relevant, or find_change_surface if you have a specific
        engineering task rather than wanting a system overview."""
        with closing(_conn()) as conn:
            return queries.list_services(conn)

    @mcp.tool()
    def describe_service(service: str, limit: int = queries.DEFAULT_LIST_LIMIT, offset: int = 0) -> dict:
        """Full picture of one microservice: description, why it calls other services/queues
        (with the business reason, data needed, and target_kind/resource_type when the
        target is external), its components (classes/controllers/modules, each with a
        summary), its APIs as one-liners, and what it persists/publishes by name only
        (use describe_persistence/describe_messages for the full field-level schema,
        including engine/provider). Includes freshness (indexed commit vs. the
        repository's current commit, and whether that means this knowledge may be
        stale). Every list (calls/apis/components/persists/messages) is capped at
        `limit` items (default 50) starting at `offset`, so a service with dozens of
        endpoints can't blow your context budget by default — the `pagination` field
        reports each list's real total and whether it was truncated; raise `offset` by
        `limit` to fetch the next page of whichever list you need more of. Call this
        once you know which service is relevant. Next: describe_api for a specific
        endpoint's contract, or get_relationships to see who else depends on it."""
        with closing(_conn()) as conn:
            return queries.describe_service(conn, service, limit, offset)

    @mcp.tool()
    def list_apis(service: str, limit: int = queries.DEFAULT_LIST_LIMIT, offset: int = 0) -> dict:
        """One-line summary of every API on one microservice — a thinner view than
        describe_service's own apis list, useful once you already know the service
        and just need the endpoint list. Capped at `limit` items (default 50) starting
        at `offset`; `total`/`truncated` in the response tell you whether to page
        further. Next: describe_api for a specific one."""
        with closing(_conn()) as conn:
            return queries.list_apis(conn, service, limit, offset)

    @mcp.tool()
    def describe_api(service: str, method: str, path: str) -> dict:
        """The most detailed level for one API: response shape field by field, its
        calls to other services/queues (business reason + exact data needed), and
        validation/authorization rules. Call this once you know exactly which
        endpoint a change touches and need its full contract before editing it."""
        with closing(_conn()) as conn:
            return queries.describe_api(conn, service, method, path)

    @mcp.tool()
    def describe_persistence(service: str, limit: int = queries.DEFAULT_LIST_LIMIT, offset: int = 0) -> dict:
        """Full field-level schema of everything one microservice persists (tables/
        documents/caches), including the concrete engine (postgres/mysql/mongodb/
        cassandra/dynamodb/redis/elasticsearch/sqlite/unknown) — describe_service only names
        these, this returns the actual fields. Capped at `limit` entities (default 50)
        starting at `offset`; `total`/`truncated` tell you whether to page further.
        Call this before changing anything that reads or writes this service's
        storage."""
        with closing(_conn()) as conn:
            return queries.describe_persistence(conn, service, limit, offset)

    @mcp.tool()
    def describe_messages(service: str, limit: int = queries.DEFAULT_LIST_LIMIT, offset: int = 0) -> dict:
        """Full field-level shape of async messages one microservice publishes/
        consumes, including the concrete broker (kafka/rabbitmq/sqs/sns/service_bus/
        activemq/nats/unknown) — describe_service only names the channels, this
        returns the actual payload shape. Capped at `limit` messages (default 50)
        starting at `offset`; `total`/`truncated` tell you whether to page further.
        Call this before changing an event's contract; then check
        get_relationships/find_change_surface's contracts_at_risk for who else
        consumes it."""
        with closing(_conn()) as conn:
            return queries.describe_messages(conn, service, limit, offset)

    @mcp.tool()
    def search(query: str) -> dict:
        """Keyword search across services, APIs, persistence entities and message
        relationships (SQLite FTS5, not semantic). Call this when you don't know
        which service/API a keyword belongs to. Next: describe_service or
        describe_api on whatever it turns up."""
        with closing(_conn()) as conn:
            return queries.search(conn, query)

    @mcp.tool()
    def get_relationships(service: str, direction: str = "both") -> dict:
        """Graph of edges around one service: outbound calls it makes, inbound calls other
        services make into it ('who depends on me'), and queue/topic links inferred from
        matching publish/consume channel names. direction: 'outbound', 'inbound' or 'both'.
        Each edge carries its business reason, confidence, provenance (llm vs.
        deterministic) and evidence (file/line) when known — this is the navigation
        primitive behind find_change_surface. Call this once you know a service and
        want its immediate neighborhood. Next: trace_flow if the service you're
        looking for isn't a direct neighbor, or describe_api on a specific edge's API."""
        with closing(_conn()) as conn:
            return queries.get_relationships(conn, service, direction)

    @mcp.tool()
    def trace_flow(from_service: str, to_service: str, max_hops: int = 6) -> dict:
        """Shortest path connecting two services, walking outbound calls and
        publish->consume message links (the multi-hop counterpart to
        get_relationships' single hop). Use this when you know two services are
        related but not how — e.g. 'does checkout-service's request ever reach
        ledger-service, and through what?'. Each hop carries its business reason and
        evidence when known. Next: describe_api/describe_messages on the hop that
        looks most relevant to your change."""
        with closing(_conn()) as conn:
            return queries.trace_flow(conn, from_service, to_service, max_hops)

    @mcp.tool()
    def find_architecture_smells() -> dict:
        """Deterministic, whole-graph structural findings computed purely from already-
        indexed facts (service_calls, persistence_entities) — no LLM call, recomputed
        after every index/update. Reports: cycle (a circular dependency among internal
        services — A depends on B depends on ... depends on A), fan_in/fan_out (a
        service with an unusually high number of direct internal dependents/
        dependencies — a bottleneck or orchestrator candidate), shared_database (two or
        more services persisting a same-named entity on the same engine — likely
        sharing a database, coupling their schemas), and duplicate_external_integration
        (two or more services independently integrating with the same third-party
        vendor). Each finding carries a plain-language reason and the services
        involved, in risk language ('likely', 'worth checking') — never a confirmed
        verdict; this describes structure, not a judgment call only a human/LLM
        synthesis over real evidence could make. When a prior run exists, the response
        also includes trend: new_findings (appeared since the last index/update),
        resolved_findings (gone since then) and count_deltas (a fan_in/fan_out count
        that changed while the finding itself persisted) — computed for free from
        already-stored runs, no re-detection. trend is omitted entirely (not an empty
        object) on the very first run ever, since there's nothing yet to compare
        against. Call this for a system-wide health check without reading any source
        file. Next: get_relationships/trace_flow on a flagged service to see the edges
        behind a finding, or describe_service to understand why it's shaped that way."""
        with closing(_conn()) as conn:
            return queries.find_architecture_smells(conn)

    @mcp.tool()
    def find_change_surface(task: str, hint_services: list[str] | None = None) -> dict:
        """Given a business task/epic description, find which indexed services likely
        need code changes — WITHOUT reading any source file. Call this FIRST when handed
        an epic, before exploring the codebase. Returns primary/secondary/no_change_hint
        service lists plus a relevant call flow, each finding with a business reason,
        confidence (0-1) and evidence (file/line); also external_integrations (third-party
        vendors/SaaS reachable from the relevant services — you may need to touch that
        integration too), unmapped_internal_hint (dependencies that look like internal
        services of this same system but haven't been indexed yet — index them for a
        fuller picture), contracts_at_risk (events a relevant service publishes and
        every other indexed service that consumes them — potentially affected, never
        declared a confirmed break) and persistence_affected (what storage each
        relevant service owns, read straight from the index, no extra call needed for
        the obvious cases). unknowns lists every gap in the answer explicitly (status,
        reason, suggestion) instead of silently omitting it — including a relevant
        service whose freshness came back stale, since its stored dependency reasons
        may themselves be out of date. freshness reports, per relevant service,
        whether its indexed knowledge might be stale (its indexed commit vs. the
        repository's current commit). recommended_next_queries is a
        ranked list of {tool, arguments, reason} — the highest-value MCP tool calls to
        make next given what's already known, computed for free with no extra LLM
        cost; prefer it over exploring blindly. This is a task-specific inference,
        not a verified fact — treat it as a starting point, not ground truth. Pass
        hint_services if you already suspect specific services, to anchor the search.
        The response includes a run_id — pass it to record_change_surface_feedback once
        you know whether the findings were actually right, to improve future confidence
        for this service. run_cost_usd reports this call's own LLM cost when the
        backend's CLI exposed it, and null (never fabricated as 0) when it didn't.
        similar_past_tasks lists up to 3 earlier find_change_surface runs whose task
        text was semantically closest to this one (only populated when the optional
        `semantic` extra is installed — empty otherwise, never an error), each with
        its own primary_services and an honest outcome ('verified precision=...
        recall=...' from a real git check, 'feedback: N confirmed, M rejected' from
        self-reported outcomes, or 'no feedback yet') — historical precedent for
        whether a similarly-worded task actually panned out."""
        with closing(_conn()) as conn:
            return queries.find_change_surface(conn, resolved_backend, task, hint_services)

    @mcp.tool()
    def record_change_surface_feedback(run_id: int, service: str, outcome: str) -> dict:
        """Report whether a find_change_surface finding was actually right: call this
        AFTER you've acted on a change surface result, once you know whether a given
        service really needed a change. outcome: 'confirmed' (it did) or 'rejected'
        (it didn't). run_id comes from a prior find_change_surface response. This
        closes the feedback loop — future find_change_surface confidence for this
        service is nudged by its track record. See also verify_change_surface,
        which computes this automatically from a real git diff instead of you
        having to know the outcome by hand."""
        with closing(_conn()) as conn:
            return queries.record_change_surface_feedback(conn, run_id, service, outcome)

    @mcp.tool()
    def verify_change_surface(run_id: int, repository: str, since_commit: str) -> dict:
        """Ground-truth check: compare a past find_change_surface run's predicted
        services against what the given repository's commits actually changed
        (git diff) since since_commit. Read-only — it does not itself record
        feedback; call record_change_surface_feedback for that. Returns predicted,
        actual, true_positives, false_positives, false_negatives, precision and
        recall. repository is the name shown by list_services'/index's
        --repository-name, not a service name."""
        with closing(_conn()) as conn:
            return queries.verify_change_surface(conn, run_id, repository, since_commit)

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(prog="impactmesh serve")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--backend", choices=["claude", "codex"], default=None, help="Used only by find_change_surface")
    parser.add_argument("--model", default=None)
    parser.add_argument("--claude-bare", action="store_true")
    parser.add_argument("--codex-api-key", action="store_true")
    args = parser.parse_args()
    backend = resolve_backend(args.backend, args.model, args.claude_bare, args.codex_api_key)
    server = build_server(args.db, backend=backend)
    server.run()


if __name__ == "__main__":
    main()
