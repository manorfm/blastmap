"""Shared Go import parsing. `engine.py`'s general symbol resolution only
ever needed a package's short name (its own `_go_imports`, unchanged), but
`cloud_detection.py`'s SDK-import verification needs the full import path —
the short name alone can't tell AWS's `.../service/sqs` apart from some
unrelated package whose own last path segment also happens to be `sqs`. One
shared regex here, not two drifting copies.
"""
from __future__ import annotations

import re

_BLOCK_RE = re.compile(r"(?ms)^\s*import\s*\((.*?)^\s*\)")
_SINGLE_RE = re.compile(r'(?m)^\s*import\s+(?:(\w+)\s+)?"([^"]+)"')
_DECLARATION_RE = re.compile(r'(?m)^\s*(?:(\w+)\s+)?"([^"]+)"')


def parse_go_import_declarations(source: str) -> list[tuple[str, str]]:
    """(alias, full import path) for every import, single-line or grouped in
    an `import ( ... )` block. `alias` is empty when the import uses its
    default (path-derived) name."""
    declarations = [
        item
        for block in _BLOCK_RE.findall(source)
        for item in _DECLARATION_RE.findall(block)
    ]
    return [*_SINGLE_RE.findall(source), *declarations]


def parse_go_import_paths(source: str) -> dict[str, str]:
    """Local alias -> full import path. `_` (blank import) and `.` (dot
    import) are excluded: neither binds a usable identifier to resolve a
    type against."""
    paths: dict[str, str] = {}
    for alias, module in parse_go_import_declarations(source):
        local_name = alias or module.rstrip("/").rsplit("/", 1)[-1]
        if local_name not in {"_", "."}:
            paths[local_name] = module
    return paths
