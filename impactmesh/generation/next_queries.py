"""Recommended next queries: turns the pieces a change surface already computed
into a small, ranked list of MCP tool calls worth making next. Every rule here is
pure lookup over already-indexed data — no extra LLM call, no re-reading source —
so this is the highest-value-per-token addition to find_change_surface: it tells an
agent exactly where to look next instead of leaving it to explore blindly."""
from __future__ import annotations

import sqlite3

from impactmesh.db.repositories import apis as apis_repo
from impactmesh.db.repositories import messages as messages_repo
from impactmesh.db.repositories import services as services_repo

DEFAULT_MAX_RECOMMENDATIONS = 5


class NextQueryRecommender:
    """One rule per kind of gap: a relevant service whose API contract wasn't
    returned in full, a relevant service with messages worth inspecting, and a
    dependency that looks internal but was never indexed. Primary services are
    considered before secondary ones, and the result is capped so the response
    stays small (see the project's context-efficiency budget tests)."""

    def __init__(self, max_recommendations: int = DEFAULT_MAX_RECOMMENDATIONS) -> None:
        self._max_recommendations = max_recommendations

    def recommend(
        self,
        conn: sqlite3.Connection | None,
        primary: list[str],
        secondary: list[str],
        unmapped_internal_hint: list[dict],
    ) -> list[dict]:
        recs: list[dict] = []
        for service_name in [*primary, *secondary]:
            recs.extend(self._recommendations_for_service(conn, service_name))
        recs.extend(self._recommendations_for_unmapped(unmapped_internal_hint))
        return recs[: self._max_recommendations]

    def _recommendations_for_service(self, conn: sqlite3.Connection, service_name: str) -> list[dict]:
        row = services_repo.get_service_by_name(conn, service_name)
        if row is None:
            return []
        recs: list[dict] = []
        apis = apis_repo.list_apis(conn, row["id"])
        if apis:
            first = apis[0]
            recs.append({
                "tool": "describe_api",
                "arguments": {"service": service_name, "method": first["method"], "path": first["path"]},
                "reason": "relevant service — inspect its API contract before changing it.",
            })
        if messages_repo.list_messages(conn, row["id"]):
            recs.append({
                "tool": "describe_messages",
                "arguments": {"service": service_name},
                "reason": "publishes or consumes messages that may need to change too.",
            })
        return recs

    def _recommendations_for_unmapped(self, unmapped_internal_hint: list[dict]) -> list[dict]:
        return [
            {
                "tool": "index",
                "arguments": {"service": hint["service"]},
                "reason": f"{hint['service']} looks internal but not indexed yet — index it for a fuller picture.",
            }
            for hint in unmapped_internal_hint
        ]
