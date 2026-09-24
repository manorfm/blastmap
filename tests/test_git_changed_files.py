"""TDD coverage for discovery.hashing.git_changed_files, the git-diff primitive
behind orbitkb's ground-truth verification (see generation/verification.py)."""
import subprocess
from pathlib import Path

from orbitkb.discovery.hashing import (
    git_changed_files,
    git_changed_files_with_status,
    git_working_changed_files_with_status,
)


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


def test_lists_files_changed_since_a_commit(tmp_path: Path):
    commit = _init_git_repo(tmp_path)
    (tmp_path / "svc-a").mkdir()
    (tmp_path / "svc-a" / "main.py").write_text("x")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "second"], cwd=tmp_path, check=True)

    changed = git_changed_files(tmp_path, commit)

    assert changed == ["svc-a/main.py"]


def test_no_changes_since_head_is_empty(tmp_path: Path):
    commit = _init_git_repo(tmp_path)

    assert git_changed_files(tmp_path, commit) == []


def test_returns_empty_for_a_bad_commit_or_non_git_dir(tmp_path: Path):
    assert git_changed_files(tmp_path, "not-a-real-commit") == []


def test_reports_a_safe_error_when_a_diff_cannot_be_read(tmp_path: Path):
    assert git_changed_files_with_status(tmp_path, "not-a-real-commit") == (
        [], "could not read the Git diff for the supplied since_commit",
    )


def test_lists_tracked_and_untracked_working_changes_since_a_commit(tmp_path: Path):
    commit = _init_git_repo(tmp_path)
    (tmp_path / "a.txt").write_text("2")
    (tmp_path / "new.py").write_text("new")

    changed, error = git_working_changed_files_with_status(tmp_path, commit)

    assert error is None
    assert changed == ["a.txt", "new.py"]
