"""Only module allowed to run SQL against the blastmap database."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from blastmap.discovery.integration_heuristics import classify_target_kind


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# repositories
# ---------------------------------------------------------------------------

def ensure_repository(conn: sqlite3.Connection, name: str, root_path: str) -> int:
    row = conn.execute("SELECT id FROM repositories WHERE root_path = ?", (root_path,)).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE repositories SET name = ?, updated_at = ? WHERE id = ?", (name, _now(), row["id"])
        )
        conn.commit()
        return row["id"]
    cur = conn.execute(
        "INSERT INTO repositories (name, root_path, updated_at) VALUES (?, ?, ?)", (name, root_path, _now())
    )
    conn.commit()
    return cur.lastrowid


def list_repositories(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM repositories ORDER BY name").fetchall()


# ---------------------------------------------------------------------------
# services
# ---------------------------------------------------------------------------

def get_service_by_name(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM services WHERE name = ?", (name,)).fetchone()


def get_service_by_id(conn: sqlite3.Connection, service_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM services WHERE id = ?", (service_id,)).fetchone()


def ensure_service(
    conn: sqlite3.Connection, name: str, root_path: str, stack: str, repository_id: int | None = None
) -> int:
    row = get_service_by_name(conn, name)
    if row is not None:
        conn.execute(
            "UPDATE services SET root_path = ?, stack = ?, updated_at = ?, "
            "repository_id = COALESCE(?, repository_id) WHERE id = ?",
            (root_path, stack, _now(), repository_id, row["id"]),
        )
        conn.commit()
        return row["id"]
    cur = conn.execute(
        "INSERT INTO services (name, root_path, stack, repository_id, updated_at) VALUES (?, ?, ?, ?, ?)",
        (name, root_path, stack, repository_id, _now()),
    )
    conn.commit()
    return cur.lastrowid


def update_service_overview(conn: sqlite3.Connection, service_id: int, short_desc: str, long_desc: str) -> None:
    conn.execute(
        "UPDATE services SET short_desc = ?, long_desc = ?, updated_at = ? WHERE id = ?",
        (short_desc, long_desc, _now(), service_id),
    )
    conn.commit()


def set_service_last_commit(conn: sqlite3.Connection, service_id: int, commit_sha: str | None) -> None:
    conn.execute("UPDATE services SET last_commit = ? WHERE id = ?", (commit_sha, service_id))
    conn.commit()


def list_services(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT s.id, s.name, s.stack, s.short_desc,
               (SELECT COUNT(*) FROM apis a WHERE a.service_id = s.id) AS api_count
        FROM services s
        ORDER BY s.name
        """
    ).fetchall()


# ---------------------------------------------------------------------------
# apis
# ---------------------------------------------------------------------------

def upsert_api(
    conn: sqlite3.Connection,
    service_id: int,
    method: str,
    path: str,
    summary: str,
    description: str,
    response_shape: dict,
    evidence: list[dict],
) -> int:
    row = conn.execute(
        "SELECT id FROM apis WHERE service_id = ? AND method = ? AND path = ?",
        (service_id, method, path),
    ).fetchone()
    payload = (summary, description, json.dumps(response_shape), json.dumps(evidence), _now())
    if row is not None:
        conn.execute(
            """UPDATE apis SET summary = ?, description = ?, response_shape = ?,
               evidence_json = ?, updated_at = ? WHERE id = ?""",
            (*payload, row["id"]),
        )
        api_id = row["id"]
    else:
        cur = conn.execute(
            """INSERT INTO apis (service_id, method, path, summary, description, response_shape,
               evidence_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (service_id, method, path, *payload),
        )
        api_id = cur.lastrowid
    conn.commit()
    return api_id


def replace_api_validations(conn: sqlite3.Connection, api_id: int, validations: list[dict]) -> None:
    conn.execute("DELETE FROM api_validations WHERE api_id = ?", (api_id,))
    conn.executemany(
        "INSERT INTO api_validations (api_id, kind, description) VALUES (?, ?, ?)",
        [(api_id, v["kind"], v["description"]) for v in validations],
    )
    conn.commit()


def replace_calls_for_api(
    conn: sqlite3.Connection, from_service_id: int, api_id: int, calls: list[dict], evidence: list[dict]
) -> None:
    conn.execute("DELETE FROM service_calls WHERE from_api_id = ?", (api_id,))
    evidence_json = json.dumps(evidence)
    conn.executemany(
        """INSERT INTO service_calls
           (from_service_id, from_api_id, to_service_name, call_kind, reason, data_needed,
            purpose_kind, confidence, target_kind, evidence_json, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                from_service_id,
                api_id,
                c["to_service_name"],
                c["call_kind"],
                c.get("reason"),
                json.dumps(c.get("data_needed", [])),
                c.get("purpose_kind"),
                c.get("confidence"),
                c.get("target_kind", "unknown"),
                evidence_json,
                _now(),
            )
            for c in calls
        ],
    )
    conn.commit()
    reconcile_service_call_targets(conn)


def prune_apis_not_in(conn: sqlite3.Connection, service_id: int, keep_keys: set[tuple[str, str]]) -> None:
    rows = conn.execute("SELECT id, method, path FROM apis WHERE service_id = ?", (service_id,)).fetchall()
    for row in rows:
        if (row["method"], row["path"]) not in keep_keys:
            conn.execute("DELETE FROM apis WHERE id = ?", (row["id"],))
    conn.commit()


def get_api_by_key(conn: sqlite3.Connection, service_id: int, method: str, path: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM apis WHERE service_id = ? AND method = ? AND path = ?", (service_id, method, path)
    ).fetchone()


def list_apis(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT method, path, summary, evidence_json FROM apis WHERE service_id = ? ORDER BY path, method",
        (service_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# service_calls (read side / reconciliation)
# ---------------------------------------------------------------------------

def reconcile_service_call_targets(conn: sqlite3.Connection) -> None:
    """Resolve to_service_id by exact name match, then refine target_kind:

    - Ground truth wins: any call that resolves to a real indexed service is
      'internal', full stop, overriding whatever the LLM guessed earlier.
    - For calls that stay unresolved and whose target_kind is still 'unknown' (the
      LLM couldn't tell from the code alone), fall back to the deterministic
      vendor-name / naming-convention heuristic — never LLM-driven, purely a name
      match against the vendor list and the shape of already-known service names.
    """
    conn.execute(
        """
        UPDATE service_calls
        SET to_service_id = (SELECT id FROM services WHERE services.name = service_calls.to_service_name)
        WHERE to_service_id IS NULL
           OR to_service_id != (SELECT id FROM services WHERE services.name = service_calls.to_service_name)
        """
    )
    conn.execute("UPDATE service_calls SET target_kind = 'internal' WHERE to_service_id IS NOT NULL")

    known_names = {row["name"] for row in conn.execute("SELECT name FROM services")}
    unresolved_unknown = conn.execute(
        "SELECT id, to_service_name FROM service_calls WHERE to_service_id IS NULL AND target_kind = 'unknown'"
    ).fetchall()
    for row in unresolved_unknown:
        kind = classify_target_kind(row["to_service_name"], known_names)
        if kind != "unknown":
            conn.execute("UPDATE service_calls SET target_kind = ? WHERE id = ?", (kind, row["id"]))

    conn.commit()


def list_calls_for_service(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT to_service_name, call_kind, reason, data_needed, purpose_kind, confidence,
                  target_kind, evidence_json
           FROM service_calls WHERE from_service_id = ? ORDER BY to_service_name""",
        (service_id,),
    ).fetchall()


def list_calls_for_api(conn: sqlite3.Connection, api_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT to_service_name, call_kind, reason, data_needed, purpose_kind, confidence,
                  target_kind, evidence_json
           FROM service_calls WHERE from_api_id = ? ORDER BY to_service_name""",
        (api_id,),
    ).fetchall()


def list_inbound_calls(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    """Calls made BY other services INTO this one — 'who depends on me'.

    Only resolves edges whose target has been reconciled to a real service_id
    (see reconcile_service_call_targets); a still-dangling to_service_name from
    an unindexed service can't be attributed to a from_service row here.
    """
    return conn.execute(
        """SELECT s.name AS from_service_name, sc.call_kind, sc.reason, sc.data_needed,
                  sc.purpose_kind, sc.confidence, sc.target_kind, sc.evidence_json
           FROM service_calls sc JOIN services s ON s.id = sc.from_service_id
           WHERE sc.to_service_id = ? ORDER BY s.name""",
        (service_id,),
    ).fetchall()


def list_external_integration_calls(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    """This service's outbound calls classified as third-party (target_kind='external')."""
    return conn.execute(
        """SELECT to_service_name, call_kind, reason, confidence, evidence_json
           FROM service_calls WHERE from_service_id = ? AND target_kind = 'external'
           ORDER BY to_service_name""",
        (service_id,),
    ).fetchall()


def list_unmapped_internal_calls(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    """This service's outbound calls that look internal but haven't been indexed yet."""
    return conn.execute(
        """SELECT to_service_name, call_kind, reason, confidence, evidence_json
           FROM service_calls
           WHERE from_service_id = ? AND target_kind = 'internal' AND to_service_id IS NULL
           ORDER BY to_service_name""",
        (service_id,),
    ).fetchall()


def list_validations_for_api(conn: sqlite3.Connection, api_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT kind, description FROM api_validations WHERE api_id = ? ORDER BY kind", (api_id,)
    ).fetchall()


# ---------------------------------------------------------------------------
# persistence & messages (one call per service -> full replace)
# ---------------------------------------------------------------------------

def replace_persistence_entities(
    conn: sqlite3.Connection, service_id: int, entities: list[dict], evidence: list[dict]
) -> None:
    conn.execute("DELETE FROM persistence_entities WHERE service_id = ?", (service_id,))
    evidence_json = json.dumps(evidence)
    conn.executemany(
        """INSERT INTO persistence_entities (service_id, name, kind, schema_json, evidence_json, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (service_id, e["name"], e.get("kind"), json.dumps(e.get("schema_json", {})), evidence_json, _now())
            for e in entities
        ],
    )
    conn.commit()


def list_persistence(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT name, kind, schema_json, evidence_json FROM persistence_entities WHERE service_id = ? ORDER BY name",
        (service_id,),
    ).fetchall()


def replace_messages(conn: sqlite3.Connection, service_id: int, messages: list[dict], evidence: list[dict]) -> None:
    conn.execute("DELETE FROM messages WHERE service_id = ?", (service_id,))
    evidence_json = json.dumps(evidence)
    conn.executemany(
        """INSERT INTO messages (service_id, direction, channel, shape_json, description, evidence_json, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                service_id,
                m["direction"],
                m["channel"],
                json.dumps(m.get("shape_json", {})),
                m.get("description"),
                evidence_json,
                _now(),
            )
            for m in messages
        ],
    )
    conn.commit()


def list_messages(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT direction, channel, shape_json, description, evidence_json
           FROM messages WHERE service_id = ? ORDER BY channel""",
        (service_id,),
    ).fetchall()


def list_message_links(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    """Other services connected to this one through a shared channel name.

    A 'publishes' row on this service links to every other service that 'consumes'
    the same channel, and vice versa. Purely a name match at query time — no extra
    storage, since channel is already the shared key both sides record.
    """
    return conn.execute(
        """
        SELECT m1.direction AS local_direction, m1.channel AS channel, s2.name AS other_service
        FROM messages m1
        JOIN messages m2 ON m2.channel = m1.channel AND m2.service_id != m1.service_id
                         AND m2.direction != m1.direction
        JOIN services s2 ON s2.id = m2.service_id
        WHERE m1.service_id = ?
        ORDER BY m1.channel, s2.name
        """,
        (service_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# indexed_files (incremental update bookkeeping)
# ---------------------------------------------------------------------------

def get_indexed_file_hashes(conn: sqlite3.Connection, service_id: int) -> dict[str, str]:
    rows = conn.execute(
        "SELECT file_path, content_hash FROM indexed_files WHERE service_id = ?", (service_id,)
    ).fetchall()
    return {row["file_path"]: row["content_hash"] for row in rows}


def set_indexed_file_hash(conn: sqlite3.Connection, service_id: int, file_path: str, content_hash: str, category: str) -> None:
    conn.execute(
        """INSERT INTO indexed_files (service_id, file_path, content_hash, category, last_indexed_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(service_id, file_path) DO UPDATE SET
             content_hash = excluded.content_hash,
             category = excluded.category,
             last_indexed_at = excluded.last_indexed_at""",
        (service_id, file_path, content_hash, category, _now()),
    )


def remove_indexed_files(conn: sqlite3.Connection, service_id: int, file_paths: Iterable[str]) -> None:
    conn.executemany(
        "DELETE FROM indexed_files WHERE service_id = ? AND file_path = ?",
        [(service_id, fp) for fp in file_paths],
    )


def commit(conn: sqlite3.Connection) -> None:
    conn.commit()


# ---------------------------------------------------------------------------
# change_surface audit trail + feedback (see generation/change_surface.py)
# ---------------------------------------------------------------------------

_ROLE_BY_RESULT_KEY = {
    "primary": "primary",
    "secondary": "secondary",
    "no_change_hint": "no_change",
    "external_integrations": "external_integration",
    "unmapped_internal_hint": "unmapped_internal",
}


def record_change_surface_run(conn: sqlite3.Connection, task_text: str, backend: str, result: dict) -> int:
    cur = conn.execute(
        "INSERT INTO change_surface_runs (task_text, backend, created_at) VALUES (?, ?, ?)",
        (task_text, backend, _now()),
    )
    run_id = cur.lastrowid
    rows = [
        (run_id, f["service"], role, f.get("reason"), f.get("confidence"), json.dumps(f.get("evidence", [])))
        for key, role in _ROLE_BY_RESULT_KEY.items()
        for f in result.get(key, [])
    ]
    conn.executemany(
        """INSERT INTO change_surface_findings (run_id, service, role, reason, confidence, evidence_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return run_id


def get_change_surface_run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM change_surface_runs WHERE id = ?", (run_id,)).fetchone()


def list_change_surface_runs(conn: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM change_surface_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def list_change_surface_findings(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM change_surface_findings WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()


def record_change_surface_feedback(conn: sqlite3.Connection, run_id: int, service: str, outcome: str) -> None:
    conn.execute(
        "INSERT INTO change_surface_feedback (run_id, service, outcome, recorded_at) VALUES (?, ?, ?, ?)",
        (run_id, service, outcome, _now()),
    )
    conn.commit()


def get_feedback_stats(conn: sqlite3.Connection, service: str) -> dict[str, int]:
    row = conn.execute(
        """SELECT
             SUM(CASE WHEN outcome = 'confirmed' THEN 1 ELSE 0 END) AS confirmed,
             SUM(CASE WHEN outcome = 'rejected' THEN 1 ELSE 0 END) AS rejected
           FROM change_surface_feedback WHERE service = ?""",
        (service,),
    ).fetchone()
    return {"confirmed": row["confirmed"] or 0, "rejected": row["rejected"] or 0}


# ---------------------------------------------------------------------------
# index_runs
# ---------------------------------------------------------------------------

def start_index_run(conn: sqlite3.Connection, service_id: int | None, backend: str) -> int:
    cur = conn.execute(
        "INSERT INTO index_runs (service_id, started_at, backend, status) VALUES (?, ?, ?, 'partial')",
        (service_id, _now(), backend),
    )
    conn.commit()
    return cur.lastrowid


def finish_index_run(
    conn: sqlite3.Connection, run_id: int, status: str, files_changed: int, llm_calls: int, notes: str | None
) -> None:
    conn.execute(
        """UPDATE index_runs SET finished_at = ?, status = ?, files_changed = ?, llm_calls = ?, notes = ?
           WHERE id = ?""",
        (_now(), status, files_changed, llm_calls, notes, run_id),
    )
    conn.commit()


def recent_index_runs(conn: sqlite3.Connection, service_id: int | None = None, limit: int = 10) -> list[sqlite3.Row]:
    if service_id is not None:
        return conn.execute(
            "SELECT * FROM index_runs WHERE service_id = ? ORDER BY id DESC LIMIT ?", (service_id, limit)
        ).fetchall()
    return conn.execute("SELECT * FROM index_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


# ---------------------------------------------------------------------------
# search (SQLite FTS5, ranked by bm25 — see rebuild_search_index* for how the
# index is populated; this section only reads it)
# ---------------------------------------------------------------------------

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
    service = get_service_by_id(conn, service_id)
    if service is None:
        return
    name = service["name"]
    conn.execute(
        "INSERT INTO search_fts (kind, service, ref, snippet, content_text, service_id) VALUES (?, ?, ?, ?, ?, ?)",
        ("service", name, name, service["short_desc"] or "",
         " ".join(filter(None, [name, service["short_desc"], service["long_desc"]])), service_id),
    )
    for a in list_apis(conn, service_id):
        ref = f"{a['method']} {a['path']}"
        conn.execute(
            "INSERT INTO search_fts (kind, service, ref, snippet, content_text, service_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("api", name, ref, a["summary"] or "",
             " ".join(filter(None, [a["summary"], a["path"]])), service_id),
        )
    for p in list_persistence(conn, service_id):
        conn.execute(
            "INSERT INTO search_fts (kind, service, ref, snippet, content_text, service_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("persistence", name, p["name"], "", p["name"], service_id),
        )
    for m in list_messages(conn, service_id):
        conn.execute(
            "INSERT INTO search_fts (kind, service, ref, snippet, content_text, service_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("message", name, m["channel"], m["description"] or "",
             " ".join(filter(None, [m["channel"], m["description"]])), service_id),
        )
    for c in list_calls_for_service(conn, service_id):
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
