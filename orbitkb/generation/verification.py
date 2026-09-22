"""Git ground-truth verification: compares a past find_change_surface run's
prediction against what a repository's commits actually changed since a given
commit, and reports precision/recall — operationalizing "was the change surface
actually right?" as a number instead of a one-off manual judgment call.

Scoped to one repository at a time deliberately: diffing across several unrelated
git histories under one `since_commit` wouldn't be meaningful, so a cumulative
multi-repository setup is verified one `orbitkb verify --repository <name>` call
per repository, exactly like indexing itself is done one repository at a time.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from orbitkb.db.repositories import change_surface as change_surface_repo
from orbitkb.db.repositories import context_telemetry as context_telemetry_repo
from orbitkb.db.repositories import repositories as repositories_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.db.repositories import verification as verification_repo
from orbitkb.discovery.hashing import git_changed_files

_PREDICTED_ROLES = {"primary", "secondary"}


def _actually_changed_services(root: Path, since_commit: str, services: list[sqlite3.Row]) -> set[str]:
    changed_files = git_changed_files(root, since_commit)
    resolved_root = root.resolve()
    changed: set[str] = set()
    for rel_path in changed_files:
        changed_abs = (resolved_root / rel_path).resolve()
        # Longest matching service root wins, so a service nested inside another
        # service's tree is attributed correctly rather than to its parent.
        best_name, best_len = None, -1
        for s in services:
            service_root = Path(s["root_path"]).resolve()
            if changed_abs == service_root or changed_abs.is_relative_to(service_root):
                if len(str(service_root)) > best_len:
                    best_name, best_len = s["name"], len(str(service_root))
        if best_name:
            changed.add(best_name)
    return changed


def verify_change_surface(
    conn: sqlite3.Connection,
    run_id: int,
    repository_name: str,
    since_commit: str,
    record_feedback: bool = False,
) -> dict:
    run = change_surface_repo.get_change_surface_run(conn, run_id)
    if run is None:
        return {"error": f"unknown change surface run_id: {run_id}"}
    repo_row = repositories_repo.get_repository_by_name(conn, repository_name)
    if repo_row is None:
        return {"error": f"unknown repository: {repository_name!r}"}

    services_in_repo = services_repo.list_services_for_repository(conn, repo_row["id"])
    names_in_repo = {s["name"] for s in services_in_repo}

    findings = change_surface_repo.list_change_surface_findings(conn, run_id)
    predicted = {f["service"] for f in findings if f["role"] in _PREDICTED_ROLES} & names_in_repo

    actual = _actually_changed_services(Path(repo_row["root_path"]), since_commit, services_in_repo)

    true_positives = sorted(predicted & actual)
    false_positives = sorted(predicted - actual)
    false_negatives = sorted(actual - predicted)

    precision = len(true_positives) / len(predicted) if predicted else None
    recall = len(true_positives) / len(actual) if actual else None

    if record_feedback:
        for service in true_positives:
            change_surface_repo.record_change_surface_feedback(conn, run_id, service, "confirmed")
        for service in false_positives:
            change_surface_repo.record_change_surface_feedback(conn, run_id, service, "rejected")

    verification_id = verification_repo.record_verification(
        conn, run_id, repository_name, since_commit, precision, recall,
        true_positives, false_positives, false_negatives,
    )

    return {
        "run_id": run_id,
        "repository": repository_name,
        "since_commit": since_commit,
        "predicted": sorted(predicted),
        "actual": sorted(actual),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": precision,
        "recall": recall,
        "verification_id": verification_id,
    }


def verify_context_budget(conn: sqlite3.Connection, context_run_id: int, repository_name: str, since_commit: str) -> dict:
    """Compare only delivered context cards against a real Git change set."""
    context_run = context_telemetry_repo.get_run(conn, context_run_id)
    if context_run is None:
        return {"error": f"unknown context run_id: {context_run_id}"}
    repo_row = repositories_repo.get_repository_by_name(conn, repository_name)
    if repo_row is None:
        return {"error": f"unknown repository: {repository_name!r}"}
    services = services_repo.list_services_for_repository(conn, repo_row["id"])
    by_id = {row["id"]: row["name"] for row in services}
    predicted_ids = set(json.loads(context_run["included_service_ids_json"])) & set(by_id)
    actual_names = _actually_changed_services(Path(repo_row["root_path"]), since_commit, services)
    actual_ids = {row["id"] for row in services if row["name"] in actual_names}
    true_positives = predicted_ids & actual_ids
    precision = len(true_positives) / len(predicted_ids) if predicted_ids else None
    recall = len(true_positives) / len(actual_ids) if actual_ids else None
    omission_rate = len(actual_ids - predicted_ids) / len(actual_ids) if actual_ids else None
    verification_id = context_telemetry_repo.record_verification(
        conn, context_run_id, repository_name, since_commit, precision, recall, omission_rate, sorted(actual_ids),
    )
    return {
        "context_run_id": context_run_id, "repository": repository_name, "since_commit": since_commit,
        "predicted": sorted(by_id[item] for item in predicted_ids), "actual": sorted(by_id[item] for item in actual_ids),
        "precision": precision, "recall": recall, "omission_rate": omission_rate, "verification_id": verification_id,
    }
