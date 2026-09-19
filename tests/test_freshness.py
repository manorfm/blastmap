"""TDD coverage for generation.freshness: whether indexed knowledge for a service
might be stale relative to the live state of its source tree. Freshness is always
derived on demand (never persisted), so it can never itself go stale."""
import subprocess
from pathlib import Path

from impactmesh.generation.freshness import compute_freshness


def _init_git_repo(root: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "a.txt").write_text("1")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "first"], cwd=root, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_matching_commits_are_not_stale(tmp_path: Path):
    commit = _init_git_repo(tmp_path)

    freshness = compute_freshness(indexed_at="2026-01-01T00:00:00+00:00", source_commit=commit, root_path=str(tmp_path))

    assert freshness["stale"] is False
    assert freshness["source_commit"] == commit
    assert freshness["current_commit"] == commit
    assert freshness["indexed_at"] == "2026-01-01T00:00:00+00:00"


def test_new_commit_since_indexing_is_stale(tmp_path: Path):
    commit = _init_git_repo(tmp_path)
    (tmp_path / "b.txt").write_text("2")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "second"], cwd=tmp_path, check=True)

    freshness = compute_freshness(indexed_at="2026-01-01T00:00:00+00:00", source_commit=commit, root_path=str(tmp_path))

    assert freshness["stale"] is True
    assert freshness["current_commit"] != commit


def test_missing_git_info_is_unknown_not_stale(tmp_path: Path):
    # Not a git repo at all, and never recorded a source_commit either.
    freshness = compute_freshness(indexed_at="2026-01-01T00:00:00+00:00", source_commit=None, root_path=str(tmp_path))

    assert freshness["stale"] is None
    assert freshness["current_commit"] is None
