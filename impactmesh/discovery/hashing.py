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
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{since_commit}..HEAD"],
            cwd=folder,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]
