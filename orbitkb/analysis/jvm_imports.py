"""Shared Java/Kotlin import parsing. Neither `engine.py` nor
`cloud_detection.py` had a JVM import resolver before this (Go has
`go_imports.py`, Node has `node_imports.py`) — this is the one place it lives
for both to reuse, not something cloud_detection.py holds alone.
"""
from __future__ import annotations

import re

_JAVA_IMPORT_RE = re.compile(r"^\s*import\s+(?!static\b)([\w.]+)\s*;", re.MULTILINE)
_KOTLIN_IMPORT_RE = re.compile(r"^\s*import\s+([\w.]+)(?:\s+as\s+(\w+))?\s*$", re.MULTILINE)


def parse_jvm_imports(source: str) -> dict[str, str]:
    """Simple class name -> fully-qualified name, covering both Java's
    `import a.b.C;` and Kotlin's `import a.b.C` / `import a.b.C as D`. A
    wildcard import (`import a.b.*;`) can't resolve to one FQN and is
    skipped — never guessed at. `import static` is skipped too: it imports a
    member, not a type."""
    imports: dict[str, str] = {}
    for fqn in _JAVA_IMPORT_RE.findall(source):
        if not fqn.endswith(".*"):
            imports[fqn.rsplit(".", 1)[-1]] = fqn
    for fqn, alias in _KOTLIN_IMPORT_RE.findall(source):
        if not fqn.endswith(".*"):
            imports[alias or fqn.rsplit(".", 1)[-1]] = fqn
    return imports
