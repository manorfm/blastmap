from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_head_commit(folder: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=folder,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def git_changed_files(folder: Path, since_commit: str) -> list[str]:
    """Files changed between since_commit and HEAD, relative to folder — the
    ground-truth signal behind verifying a past find_change_surface prediction
    (see generation/verification.py). Empty on any git failure (bad commit, folder
    no longer a git repo, etc.) rather than raising: verification is best-effort."""
    changed_files, _error = git_changed_files_with_status(folder, since_commit)
    return changed_files


def git_changed_files_with_status(folder: Path, since_commit: str) -> tuple[list[str], str | None]:
    """Return changed paths or a safe error instead of treating a failed diff as empty."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{since_commit}..HEAD"],
            cwd=folder,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return [], "could not read the Git diff"
    if result.returncode != 0:
        return [], "could not read the Git diff for the supplied since_commit"
    return [line for line in result.stdout.splitlines() if line], None


def git_working_changed_files_with_status(folder: Path, since_commit: str) -> tuple[list[str], str | None]:
    """Return files changed from a base commit, including local and untracked work."""
    try:
        diff_result = subprocess.run(
            ["git", "diff", "--name-only", since_commit],
            cwd=folder,
            capture_output=True,
            text=True,
            timeout=5,
        )
        untracked_result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=folder,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return [], "could not read the Git working diff"
    if diff_result.returncode != 0 or untracked_result.returncode != 0:
        return [], "could not read the Git working diff for the supplied since_commit"
    return sorted({
        *diff_result.stdout.splitlines(),
        *untracked_result.stdout.splitlines(),
    } - {""}), None
