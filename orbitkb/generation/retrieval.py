"""Candidate retrieval strategies for find_change_surface: turn a free-text task
into a bounded list of already-indexed candidate service names, without ever
reading source code. Kept separate from generation/change_surface.py's
synthesis/filtering logic (Strategy pattern) so a different retrieval approach —
e.g. a future semantic one — can be swapped in without touching it."""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Protocol

from orbitkb.db.repositories import embeddings as embeddings_repo
from orbitkb.db.repositories import messages as messages_repo
from orbitkb.db.repositories import search as search_repo
from orbitkb.db.repositories import service_calls as service_calls_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.generation.embeddings import EmbeddingBackend, cosine_similarity

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


class CandidateRetrieval(Protocol):
    def candidates(
        self,
        conn: sqlite3.Connection,
        task: str,
        hint_services: list[str] | None,
        max_candidates: int,
        repository_id: int | None = None,
    ) -> list[str]: ...


class KeywordGraphRetrieval:
    """Keyword-matches the task against the FTS5 index for seed services, then
    breadth-first walks the relationship graph (outbound calls, inbound calls,
    message links) a couple of hops out from those seeds, so a service connected
    only through an intermediate one still surfaces. Still pure SQL — no source
    file is read to compute this.
    """

    def __init__(self, hops: int = 2) -> None:
        self._hops = hops

    def candidates(
        self,
        conn: sqlite3.Connection,
        task: str,
        hint_services: list[str] | None,
        max_candidates: int,
        repository_id: int | None = None,
    ) -> list[str]:
        seeds = self._seed_services(conn, task, repository_id)
        if hint_services:
            seeds |= set(hint_services)
        if not seeds:
            return []
        return self._expand_candidates(conn, seeds, max_candidates, repository_id)

    def _seed_services(self, conn: sqlite3.Connection, task: str, repository_id: int | None) -> set[str]:
        keywords = _extract_keywords(task)
        if not keywords:
            return set()
        # search()'s own _build_fts_query already ORs every token in whatever string
        # it's given into one MATCH query, so passing all keywords at once here
        # reproduces the same OR-matching semantics as searching each individually,
        # in one round trip instead of N. The limit scales with keyword count so a
        # task with several distinct keywords doesn't get capped below what the old
        # per-keyword loop (limit=20 each) would have found in aggregate.
        limit = 20 * len(keywords)
        if repository_id is None:
            rows = search_repo.search(conn, " ".join(keywords), limit=limit)
        else:
            rows = search_repo.search(conn, " ".join(keywords), limit=limit, repository_id=repository_id)
        return {r["service"] for r in rows}

    def _expand_candidates(
        self, conn: sqlite3.Connection, seeds: set[str], max_candidates: int, repository_id: int | None,
    ) -> list[str]:
        visited: set[str] = set()
        frontier: set[str] = set(seeds)
        for _ in range(self._hops):
            if not frontier or len(visited) >= max_candidates:
                break
            newly_found: set[str] = set()
            for name in frontier:
                visited.add(name)
                row = services_repo.get_service_by_name(conn, name, repository_id=repository_id)
                if row is None:
                    continue
                for c in service_calls_repo.list_calls_for_service(conn, row["id"]):
                    newly_found.add(c["to_service_name"])
                for c in service_calls_repo.list_inbound_calls(conn, row["id"]):
                    newly_found.add(c["from_service_name"])
                for link in messages_repo.list_message_links(conn, row["id"]):
                    newly_found.add(link["other_service"])
            frontier = newly_found - visited
        visited |= frontier  # include the last frontier even though its own edges go unexplored

        # Only keep names that resolve to a real indexed service — a dangling
        # to_service_name with no matching row can't be given any context afterward.
        known = [
            name for name in visited
            if services_repo.get_service_by_name(conn, name, repository_id=repository_id) is not None
        ]
        return sorted(known)[:max_candidates]


class SemanticRetrieval:
    """Cosine-ranks the task against every indexed service's local embedding (see
    generation/embeddings.py, db/repositories/embeddings.py). It can complement
    KeywordGraphRetrieval without a model call, but does cost one local encode.

    SIMILARITY_FLOOR is a starting constant, not a trained value — same honesty as
    generation.architecture.FAN_THRESHOLD.
    """

    SIMILARITY_FLOOR = 0.35

    def __init__(self, embedding_backend: EmbeddingBackend) -> None:
        self._embedding_backend = embedding_backend

    def candidates(
        self,
        conn: sqlite3.Connection,
        task: str,
        hint_services: list[str] | None,
        max_candidates: int,
        repository_id: int | None = None,
    ) -> list[str]:
        hints = [
            name for name in (hint_services or [])
            if services_repo.get_service_by_name(conn, name, repository_id=repository_id) is not None
        ]
        rows = embeddings_repo.get_all_service_embeddings(conn)
        if not rows:
            return hints[:max_candidates]

        task_vector = self._embedding_backend.embed([task])[0]
        scored = [
            (cosine_similarity(task_vector, json.loads(row["vector_json"])), row["service_name"])
            for row in rows
            if repository_id is None or row["repository_id"] == repository_id
        ]
        ranked = [name for score, name in sorted(scored, key=lambda item: item[0], reverse=True) if score >= self.SIMILARITY_FLOOR]

        combined = hints + [name for name in ranked if name not in hints]
        return combined[:max_candidates]


class HybridRetrieval:
    """Blend bounded lexical and semantic candidate lists without losing either.

    Each strategy sees the same cap, then their candidates are interleaved. This
    preserves progressive-disclosure limits while keeping a semantic match visible
    when lexical graph expansion finds many related services.
    """

    def __init__(self, lexical: CandidateRetrieval, semantic: CandidateRetrieval) -> None:
        self._lexical = lexical
        self._semantic = semantic

    def candidates(
        self,
        conn: sqlite3.Connection,
        task: str,
        hint_services: list[str] | None,
        max_candidates: int,
        repository_id: int | None = None,
    ) -> list[str]:
        lexical = self._lexical.candidates(conn, task, hint_services, max_candidates, repository_id)
        semantic = self._semantic.candidates(conn, task, hint_services, max_candidates, repository_id)
        combined: list[str] = []
        for index in range(max(len(lexical), len(semantic))):
            for candidates in (lexical, semantic):
                if index >= len(candidates) or candidates[index] in combined:
                    continue
                combined.append(candidates[index])
                if len(combined) == max_candidates:
                    return combined
        return combined


class FallbackRetrieval:
    """Decorator (GoF): tries `primary` first, only calls `secondary` when it
    returns no candidates at all — the default (keyword-only) path pays zero extra
    cost or latency."""

    def __init__(self, primary: CandidateRetrieval, secondary: CandidateRetrieval) -> None:
        self._primary = primary
        self._secondary = secondary

    def candidates(
        self,
        conn: sqlite3.Connection,
        task: str,
        hint_services: list[str] | None,
        max_candidates: int,
        repository_id: int | None = None,
    ) -> list[str]:
        found = self._primary.candidates(conn, task, hint_services, max_candidates, repository_id)
        if found:
            return found
        return self._secondary.candidates(conn, task, hint_services, max_candidates, repository_id)
