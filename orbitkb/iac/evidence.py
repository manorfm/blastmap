"""Shared "locate a line for an already-known-true fact" helper for IaC parsers.

python-hcl2 and PyYAML both discard line numbers from their public parse API.
Once a parser already knows a resource's identity from the structured parse
(the source of truth for *what* the resource is), this narrows a search over
the raw file text for that resource's own literal declaration header — never to
decide what the resource is, only to locate where it lives. Mirrors how
orbitkb/analysis/engine.py's own evidence helpers relate to tree-sitter node
spans: regex only for span-finding, never for classification.
"""
from __future__ import annotations

import re


def find_line(text: str, pattern: re.Pattern[str]) -> int:
    """1-indexed line of the first match, or 1 when the header can't be found
    (e.g. unusual formatting) — never a failure, evidence is best-effort."""
    match = pattern.search(text)
    if not match:
        return 1
    return text.count("\n", 0, match.start()) + 1
