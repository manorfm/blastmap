"""Persistence for privacy-safe get_change_context calibration metadata."""
from __future__ import annotations

import hashlib
import json
import sqlite3

from ._util import now


def record_run(conn: sqlite3.Connection, metadata: dict) -> int:
    cur = conn.execute(
        """INSERT INTO context_budget_runs (
               change_surface_run_id, repository_id, epic_type, requested_budget, returned_cards,
               candidate_count, truncated, response_bytes, estimated_tokens,
               included_service_ids_json, omitted_service_ids_json, candidate_ranking_json,
               recommended_queries_json, created_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            metadata["change_surface_run_id"], metadata.get("repository_id"),
            metadata["epic_type"],
            metadata["requested_budget"], metadata["returned_cards"], metadata["candidate_count"],
            int(metadata["truncated"]), metadata["response_bytes"], metadata["estimated_tokens"],
            json.dumps(metadata["included_service_ids"]), json.dumps(metadata["omitted_service_ids"]),
            json.dumps(metadata["candidate_ranking"]), json.dumps(metadata["recommended_queries"]), now(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def update_response_measurements(conn: sqlite3.Connection, run_id: int, response_bytes: int, estimated_tokens: int) -> None:
    conn.execute(
        "UPDATE context_budget_runs SET response_bytes = ?, estimated_tokens = ? WHERE id = ?",
        (response_bytes, estimated_tokens, run_id),
    )
    conn.commit()


def get_run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM context_budget_runs WHERE id = ?", (run_id,)).fetchone()


def record_feedback(
    conn: sqlite3.Connection,
    run_id: int,
    outcome: str,
    note: str | None,
    missing_service_ids: list[int],
) -> None:
    digest = hashlib.sha256(note.encode("utf-8")).hexdigest() if note else None
    conn.execute(
        """INSERT INTO context_budget_feedback
           (context_run_id, outcome, note_digest, missing_service_ids_json, recorded_at)
           VALUES (?, ?, ?, ?, ?)""",
        (run_id, outcome, digest, json.dumps(missing_service_ids), now()),
    )
    conn.commit()


def list_feedback(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT outcome, note_digest, missing_service_ids_json, recorded_at FROM context_budget_feedback "
        "WHERE context_run_id = ? ORDER BY id", (run_id,)
    ).fetchall()


def record_query_execution(conn: sqlite3.Connection, run_id: int, tool: str, service_id: int | None) -> None:
    conn.execute(
        """INSERT INTO context_budget_query_executions (context_run_id, tool, service_id, executed_at)
           VALUES (?, ?, ?, ?)""", (run_id, tool, service_id, now())
    )
    conn.commit()


def record_verification(
    conn: sqlite3.Connection, run_id: int, repository: str, since_commit: str,
    precision: float | None, recall: float | None, omission_rate: float | None, actual_service_ids: list[int],
) -> int:
    cur = conn.execute(
        """INSERT INTO context_budget_verifications
           (context_run_id, repository, since_commit, precision, recall, omission_rate, actual_service_ids_json, verified_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, repository, since_commit, precision, recall, omission_rate, json.dumps(actual_service_ids), now()),
    )
    conn.commit()
    return cur.lastrowid


def aggregate(conn: sqlite3.Connection, epic_type: str | None = None) -> dict:
    where = "WHERE epic_type = ?" if epic_type is not None else ""
    params = (epic_type,) if epic_type is not None else ()
    totals = conn.execute(
        """SELECT COUNT(*) AS runs, AVG(response_bytes) AS average_response_bytes,
                  AVG(estimated_tokens) AS average_estimated_tokens,
                  AVG(truncated) AS truncation_rate
           FROM context_budget_runs """ + where, params).fetchone()
    budgets = conn.execute(
        """SELECT requested_budget AS budget, COUNT(*) AS runs, AVG(returned_cards) AS average_returned_cards
           FROM context_budget_runs """ + where + " GROUP BY requested_budget ORDER BY requested_budget", params).fetchall()
    outcomes = conn.execute(
        """SELECT feedback.outcome, COUNT(*) AS count FROM context_budget_feedback AS feedback
           JOIN context_budget_runs AS run ON run.id = feedback.context_run_id """ + where.replace("epic_type", "run.epic_type") +
        " GROUP BY feedback.outcome", params
    ).fetchall()
    recommendation_count = conn.execute(
        "SELECT COALESCE(SUM(json_array_length(recommended_queries_json)), 0) AS count FROM context_budget_runs " + where, params
    ).fetchone()["count"]
    executed_count = conn.execute(
        """SELECT COUNT(*) AS count FROM context_budget_query_executions AS execution
           JOIN context_budget_runs AS run ON run.id = execution.context_run_id """ + where.replace("epic_type", "run.epic_type"), params
    ).fetchone()["count"]
    verification = conn.execute(
        """SELECT COUNT(*) AS runs, AVG(verification.precision) AS precision,
                  AVG(verification.recall) AS recall, AVG(verification.omission_rate) AS omission_rate
           FROM context_budget_verifications AS verification
           JOIN context_budget_runs AS run ON run.id = verification.context_run_id """ + where.replace("epic_type", "run.epic_type"), params
    ).fetchone()
    by_type_rows = conn.execute(
        """SELECT run.epic_type, feedback.outcome, COUNT(*) AS count
           FROM context_budget_feedback AS feedback
           JOIN context_budget_runs AS run ON run.id = feedback.context_run_id
           GROUP BY run.epic_type, feedback.outcome ORDER BY run.epic_type"""
    ).fetchall()
    by_epic_type: dict[str, dict[str, int]] = {}
    for row in by_type_rows:
        by_epic_type.setdefault(row["epic_type"], {"sufficient": 0, "insufficient": 0, "excessive": 0})[row["outcome"]] = row["count"]
    return {
        "runs": totals["runs"],
        "average_response_bytes": round(totals["average_response_bytes"] or 0),
        "average_estimated_tokens": round(totals["average_estimated_tokens"] or 0),
        "truncation_rate": round(totals["truncation_rate"] or 0, 3),
        "budget_distribution": [
            {"budget": row["budget"], "runs": row["runs"], "average_returned_cards": round(row["average_returned_cards"], 2)}
            for row in budgets
        ],
        "sufficiency": {outcome: next((row["count"] for row in outcomes if row["outcome"] == outcome), 0)
                       for outcome in ("sufficient", "insufficient", "excessive")},
        "recommended_queries": recommendation_count,
        "executed_recommended_queries": executed_count,
        "git_verification": {
            "runs": verification["runs"],
            "precision": round(verification["precision"], 3) if verification["precision"] is not None else None,
            "recall": round(verification["recall"], 3) if verification["recall"] is not None else None,
            "omission_rate": round(verification["omission_rate"], 3) if verification["omission_rate"] is not None else None,
        },
        "by_epic_type": by_epic_type,
    }
