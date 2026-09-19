"""Candidate retrieval strategies for find_change_surface: turn a free-text task
into a bounded list of already-indexed candidate service names, without ever
reading source code. Kept separate from generation/change_surface.py's
synthesis/filtering logic (Strategy pattern) so a different retrieval approach —
e.g. a future semantic one — can be swapped in without touching it."""
from __future__ import annotations

import re
import sqlite3
from typing import Protocol

from orbitkb.db.repositories import messages as messages_repo
from orbitkb.db.repositories import search as search_repo
from orbitkb.db.repositories import service_calls as service_calls_repo
from orbitkb.db.repositories import services as services_repo

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
    ) -> list[str]:
        seeds = self._seed_services(conn, task)
        if hint_services:
            seeds |= set(hint_services)
        if not seeds:
            return []
        return self._expand_candidates(conn, seeds, max_candidates)

    def _seed_services(self, conn: sqlite3.Connection, task: str) -> set[str]:
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
        return {r["service"] for r in search_repo.search(conn, " ".join(keywords), limit=limit)}

    def _expand_candidates(self, conn: sqlite3.Connection, seeds: set[str], max_candidates: int) -> list[str]:
        visited: set[str] = set()
        frontier: set[str] = set(seeds)
        for _ in range(self._hops):
            if not frontier or len(visited) >= max_candidates:
                break
            newly_found: set[str] = set()
            for name in frontier:
                visited.add(name)
                row = services_repo.get_service_by_name(conn, name)
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
        known = [name for name in visited if services_repo.get_service_by_name(conn, name) is not None]
        return sorted(known)[:max_candidates]
