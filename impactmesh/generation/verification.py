"""Git ground-truth verification: compares a past find_change_surface run's
prediction against what a repository's commits actually changed since a given
commit, and reports precision/recall — operationalizing "was the change surface
actually right?" as a number instead of a one-off manual judgment call.

Scoped to one repository at a time deliberately: diffing across several unrelated
git histories under one `since_commit` wouldn't be meaningful, so a cumulative
multi-repository setup is verified one `impactmesh verify --repository <name>` call
per repository, exactly like indexing itself is done one repository at a time.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from impactmesh.db.repositories import change_surface as change_surface_repo
from impactmesh.db.repositories import repositories as repositories_repo
from impactmesh.db.repositories import services as services_repo
from impactmesh.db.repositories import verification as verification_repo
from impactmesh.discovery.hashing import git_changed_files

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
