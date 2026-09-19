"""Freshness: whether the indexed knowledge for a service might be stale relative
to the live state of its source tree. Always derived on demand from
services.last_commit vs a fresh `git rev-parse HEAD` — never persisted, so the
freshness value itself can never go stale."""
from __future__ import annotations

from pathlib import Path

from impactmesh.discovery.hashing import git_head_commit


def compute_freshness(indexed_at: str | None, source_commit: str | None, root_path: str) -> dict:
    current_commit = git_head_commit(Path(root_path))
    if source_commit is None or current_commit is None:
        stale = None  # can't determine: never indexed with a commit, or root isn't a git repo anymore
    else:
        stale = source_commit != current_commit
    return {
        "indexed_at": indexed_at,
        "source_commit": source_commit,
        "current_commit": current_commit,
        "stale": stale,
    }
