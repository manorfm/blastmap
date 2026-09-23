"""Shared Node/TS named-import parsing. Both `engine.py` (general symbol/
call resolution) and `cloud_detection.py` (SDK-import verification) need the
same "which module did this locally-bound name come from" fact — this is the
one place that regex lives, instead of two near-identical copies drifting
apart from each other.
"""
from __future__ import annotations

import re
from pathlib import Path

_IMPORT_RE = re.compile(r"import\s*\{([^}]+)\}\s*from\s*[\"']([^\"']+)[\"']")


def parse_node_named_imports(source: str) -> list[tuple[str, str, str]]:
    """(local_name, module_basename, original_name) for every named import,
    in source order. `module_basename` strips a scoped package's `@scope/`
    segment the same way `@aws-sdk/client-sqs` becomes `client-sqs`. An
    aliased import (`{ X as Y }`) resolves `local_name` to `Y` while keeping
    `original_name` as `X`, so a caller can still match on the real exported
    symbol regardless of what the importing file calls it locally."""
    parsed: list[tuple[str, str, str]] = []
    for names, module in _IMPORT_RE.findall(source):
        module_name = Path(module).name
        for item in names.split(","):
            original, _as, local = item.strip().partition(" as ")
            original = original.strip()
            if original:
                parsed.append(((local or original).strip(), module_name, original))
    return parsed
