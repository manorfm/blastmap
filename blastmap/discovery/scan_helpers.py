"""Small shared helpers used by every stack detector so each one stays a short list
of regex patterns rather than reimplementing file walking / excerpt slicing."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from blastmap.discovery.base import CodeExcerpt

SKIP_DIRS = {
    "node_modules", ".venv", "venv", "env", "dist", "build", ".git", "target",
    "__pycache__", ".idea", ".gradle", "vendor", "bin", "obj", ".mypy_cache",
    ".pytest_cache", "coverage", ".next", ".turbo",
}

MAX_FILE_BYTES = 300_000  # skip generated/huge files

# Endpoint hints anchor on the route decorator/annotation line, whose interesting
# content (the handler body) lies almost entirely after it — so use a small
# look-back and a generous look-ahead rather than a symmetric window.
ENDPOINT_BEFORE = 3
ENDPOINT_AFTER = 60


def iter_files(folder: Path, extensions: tuple[str, ...]) -> Iterator[Path]:
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in extensions:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(folder).parts):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        yield path


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def excerpt_around(
    path: Path,
    folder: Path,
    line_no: int,
    context: int = 15,
    before: int | None = None,
    after: int | None = None,
) -> CodeExcerpt:
    """Slice a window of lines around line_no.

    context sets a symmetric window by default. Pass before/after explicitly for an
    asymmetric window — e.g. a route decorator/annotation line has its interesting
    content (the whole handler body) almost entirely AFTER it, so endpoint hints use a
    small `before` and a large `after` rather than wasting half the budget on the
    (usually irrelevant) lines above the decorator.
    """
    text = read_text(path) or ""
    lines = text.splitlines()
    b = context if before is None else before
    a = context if after is None else after
    start = max(1, line_no - b)
    end = min(len(lines), line_no + a)
    snippet = "\n".join(lines[start - 1 : end])
    rel = path.relative_to(folder).as_posix()
    return CodeExcerpt(file_path=rel, start_line=start, end_line=end, text=snippet)


def find_matches(folder: Path, extensions: tuple[str, ...], pattern: re.Pattern[str]) -> list[tuple[Path, int, re.Match[str]]]:
    hits: list[tuple[Path, int, re.Match[str]]] = []
    for path in iter_files(folder, extensions):
        text = read_text(path)
        if not text:
            continue
        for match in pattern.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            hits.append((path, line_no, match))
    return hits


def first_existing_file(folder: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return None
