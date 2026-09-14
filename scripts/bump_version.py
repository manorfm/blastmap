#!/usr/bin/env python3
"""Bumps the project's semantic version from a Conventional Commits message.

Invoked by the commit-msg git hook (scripts/git-hooks/commit-msg), never by hand
except to test it. Rules (matching Conventional Commits / semantic-release):
  - "feat: ..."            -> minor bump
  - "fix: ..." / "perf:"   -> patch bump
  - "feat!: ..." or a
    "BREAKING CHANGE:"     -> major bump
    footer/body line
  - anything else
    (chore/docs/refactor/
    style/test/build/ci,
    or a non-conventional
    message)               -> no bump

On a bump, updates the version string in both pyproject.toml and
context_insight/__init__.py and stages them (git add) so they land in the same
commit that triggered the bump.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
INIT_FILE = REPO_ROOT / "context_insight" / "__init__.py"

CONVENTIONAL_RE = re.compile(r"^(?P<type>\w+)(?:\([^)]*\))?(?P<breaking>!)?:\s")

# Commit sources where re-running the bump would be wrong or meaningless:
# amends/reuses an existing message ("commit"), or merge/squash commits.
SKIP_SOURCES = {"merge", "squash", "commit"}


def bump_kind(message: str) -> str | None:
    stripped = message.strip()
    if not stripped:
        return None
    first_line = stripped.splitlines()[0]
    match = CONVENTIONAL_RE.match(first_line)
    if not match:
        return None
    if match.group("breaking") or "BREAKING CHANGE" in message:
        return "major"
    commit_type = match.group("type").lower()
    if commit_type == "feat":
        return "minor"
    if commit_type in ("fix", "perf"):
        return "patch"
    return None


def next_version(current: str, kind: str) -> str:
    major, minor, patch = (int(p) for p in current.split("."))
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def read_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("bump_version: could not find `version = \"...\"` in pyproject.toml")
    return match.group(1)


def write_version(new_version: str) -> None:
    targets = (
        (PYPROJECT, r'^version\s*=\s*"[^"]+"', f'version = "{new_version}"'),
        (INIT_FILE, r'^__version__\s*=\s*"[^"]+"', f'__version__ = "{new_version}"'),
    )
    for path, pattern, replacement in targets:
        text = path.read_text(encoding="utf-8")
        new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
        if count == 0:
            raise SystemExit(f"bump_version: could not find version line in {path}")
        path.write_text(new_text, encoding="utf-8")
        subprocess.run(["git", "add", str(path)], check=True, cwd=REPO_ROOT)


def main() -> int:
    if len(sys.argv) < 2:
        print("bump_version: missing commit message file path, skipping", file=sys.stderr)
        return 0  # never block a commit over a hook wiring mistake

    msg_file = Path(sys.argv[1])
    commit_source = sys.argv[2] if len(sys.argv) > 2 else ""
    if commit_source in SKIP_SOURCES:
        return 0

    message = msg_file.read_text(encoding="utf-8")
    if message.lstrip().startswith("Merge "):
        return 0

    kind = bump_kind(message)
    if kind is None:
        return 0

    current = read_version()
    new_version = next_version(current, kind)
    write_version(new_version)
    print(f"bump_version: {current} -> {new_version} ({kind} bump, conventional commit)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
