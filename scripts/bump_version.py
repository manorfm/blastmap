#!/usr/bin/env python3
"""Bumps the project's semantic version as a deliberate release step. Run by hand:

    python scripts/bump_version.py <major|minor|patch>

Updates the version string in both pyproject.toml and orbitkb/__init__.py.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
INIT_FILE = REPO_ROOT / "orbitkb" / "__init__.py"

_KINDS = ("major", "minor", "patch")


def next_version(current: str, kind: str) -> str:
    if kind not in _KINDS:
        raise ValueError(f"unknown bump kind: {kind!r} (expected one of {_KINDS})")
    major, minor, patch = (int(p) for p in current.split("."))
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def read_version(pyproject_path: Path) -> str:
    text = pyproject_path.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit(f'bump_version: could not find `version = "..."` in {pyproject_path}')
    return match.group(1)


def write_version(pyproject_path: Path, init_path: Path, new_version: str) -> None:
    for path, pattern, replacement in (
        (pyproject_path, r'^version\s*=\s*"[^"]+"', f'version = "{new_version}"'),
        (init_path, r'^__version__\s*=\s*"[^"]+"', f'__version__ = "{new_version}"'),
    ):
        text = path.read_text(encoding="utf-8")
        new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
        if count == 0:
            raise SystemExit(f"bump_version: could not find version line in {path}")
        path.write_text(new_text, encoding="utf-8")


def bump(pyproject_path: Path, init_path: Path, kind: str) -> tuple[str, str]:
    current = read_version(pyproject_path)
    new_version = next_version(current, kind)
    write_version(pyproject_path, init_path, new_version)
    return current, new_version


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in _KINDS:
        print(f"usage: bump_version.py <{'|'.join(_KINDS)}>", file=sys.stderr)
        return 1
    current, new_version = bump(PYPROJECT, INIT_FILE, sys.argv[1])
    # Human summary on stderr, bare version alone on stdout: a caller can capture the
    # result with plain shell command substitution without parsing a log line.
    print(f"bump_version: {current} -> {new_version}", file=sys.stderr)
    print(new_version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
