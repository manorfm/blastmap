"""The `search_fts` virtual table: a full-text index (SQLite FTS5, bm25-ranked)
rebuilt explicitly per service after each indexing write (not kept in sync via
triggers — every write path already replaces rows in bulk per service, so an
explicit rebuild after each service's writes is simpler and cheap at this
project's scale). Reads across the other repository modules to populate one
denormalized, searchable row per service/API/persistence entity/message/relationship."""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from . import apis as apis_repo
from . import messages as messages_repo
from . import persistence as persistence_repo
from . import service_calls as service_calls_repo
from . import services as services_repo

_FTS_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


def _build_fts_query(query: str) -> str | None:
    # Tokens are pre-filtered to [a-zA-Z0-9]+ so they're always safe as bare FTS5
    # tokens (no quoting needed). The trailing * makes each a prefix match, since
    # FTS5 tokens match whole words by default and the old LIKE-based search's
    # substring behavior (e.g. "charge" hitting "Charges") should still work.
    tokens = _FTS_TOKEN_RE.findall(query)
    if not tokens:
        return None
    return " OR ".join(f"{t}*" for t in tokens)


def search(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[dict[str, Any]]:
    fts_query = _build_fts_query(query)
    if fts_query is None:
        return []
    rows = conn.execute(
        """SELECT kind, service, ref, snippet FROM search_fts
           WHERE search_fts MATCH ? ORDER BY bm25(search_fts) LIMIT ?""",
        (fts_query, limit),
    ).fetchall()
    return [{"kind": r["kind"], "service": r["service"], "ref": r["ref"], "snippet": r["snippet"] or ""} for r in rows]


def _populate_search_index_for_service(conn: sqlite3.Connection, service_id: int) -> None:
    service = services_repo.get_service_by_id(conn, service_id)
    if service is None:
        return
    name = service["name"]
    conn.execute(
        "INSERT INTO search_fts (kind, service, ref, snippet, content_text, service_id) VALUES (?, ?, ?, ?, ?, ?)",
        ("service", name, name, service["short_desc"] or "",
         " ".join(filter(None, [name, service["short_desc"], service["long_desc"]])), service_id),
    )
    for a in apis_repo.list_apis(conn, service_id):
        ref = f"{a['method']} {a['path']}"
        conn.execute(
            "INSERT INTO search_fts (kind, service, ref, snippet, content_text, service_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("api", name, ref, a["summary"] or "",
             " ".join(filter(None, [a["summary"], a["path"]])), service_id),
        )
    for p in persistence_repo.list_persistence(conn, service_id):
        conn.execute(
            "INSERT INTO search_fts (kind, service, ref, snippet, content_text, service_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("persistence", name, p["name"], "", p["name"], service_id),
        )
    for m in messages_repo.list_messages(conn, service_id):
        conn.execute(
            "INSERT INTO search_fts (kind, service, ref, snippet, content_text, service_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("message", name, m["channel"], m["description"] or "",
             " ".join(filter(None, [m["channel"], m["description"]])), service_id),
        )
    for c in service_calls_repo.list_calls_for_service(conn, service_id):
        conn.execute(
            "INSERT INTO search_fts (kind, service, ref, snippet, content_text, service_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("relationship", name, c["to_service_name"], c["reason"] or "",
             " ".join(filter(None, [c["to_service_name"], c["reason"], c["purpose_kind"]])), service_id),
        )


def rebuild_search_index_for_service(conn: sqlite3.Connection, service_id: int) -> None:
    conn.execute("DELETE FROM search_fts WHERE service_id = ?", (service_id,))
    _populate_search_index_for_service(conn, service_id)
    conn.commit()


def rebuild_search_index(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM search_fts")
    for row in conn.execute("SELECT id FROM services"):
        _populate_search_index_for_service(conn, row["id"])
    conn.commit()
