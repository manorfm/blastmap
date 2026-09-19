#!/usr/bin/env python3
"""Ensures README.md has an up-to-date badges block right under the title.

Run as part of `make release`, before the release commit. Idempotent: rerun
without any change to pyproject.toml's project metadata and README.md gets no
diff. Badges are all dynamic shields.io endpoints (PyPI, GitHub Actions) that
pull live data, so there's no version number baked into the README to drift.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"

START = "<!-- BADGES:START -->"
END = "<!-- BADGES:END -->"


def badges_block() -> str:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]
    name = project["name"]
    repo_url = project["urls"]["Repository"].rstrip("/")
    owner_repo = repo_url.split("github.com/", 1)[1]

    lines = [
        START,
        f"[![PyPI](https://img.shields.io/pypi/v/{name}.svg)]"
        f"(https://pypi.org/project/{name}/)",
        f"[![Python versions](https://img.shields.io/pypi/pyversions/{name}.svg)]"
        f"(https://pypi.org/project/{name}/)",
        f"[![CI](https://github.com/{owner_repo}/actions/workflows/ci.yml/badge.svg)]"
        f"(https://github.com/{owner_repo}/actions/workflows/ci.yml)",
        f"[![Security](https://github.com/{owner_repo}/actions/workflows/security.yml/badge.svg)]"
        f"(https://github.com/{owner_repo}/actions/workflows/security.yml)",
        f"[![License](https://img.shields.io/pypi/l/{name}.svg)]"
        f"(https://pypi.org/project/{name}/)",
        END,
    ]
    return "\n".join(lines)


def ensure(text: str) -> str:
    block = badges_block()
    if START in text and END in text:
        pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)
        return pattern.sub(block, text, count=1)

    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("# "):
            j = i + 1
            while j < len(lines) and lines[j] == "":
                j += 1
            lines[i + 1 : j] = ["", block, ""]
            return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    raise SystemExit("ensure_badges: no H1 title found in README.md")


def main() -> int:
    text = README.read_text(encoding="utf-8")
    new_text = ensure(text)
    if new_text != text:
        README.write_text(new_text, encoding="utf-8")
        print("ensure_badges: README.md updated")
    else:
        print("ensure_badges: README.md already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
