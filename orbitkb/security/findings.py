"""Deterministic security findings that never retain a secret value."""
from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from orbitkb.discovery.scan_helpers import iter_files, read_text

_SOURCE_EXTENSIONS = (".py", ".go", ".java", ".kt", ".js", ".jsx", ".ts", ".tsx")
_SECRET_LITERAL = re.compile(
    r"(?i)\b\w*(?:password|secret|token|api[_-]?key|credential)\w*\s*[:=]\s*['\"][^'\"]+['\"]"
)


@dataclass(frozen=True)
class SecurityFinding:
    kind: str
    severity: str
    file_path: str
    line: int
    reason: str


def find_security_findings(root: Path, is_tracked: Callable[[Path], bool] | None = None) -> list[SecurityFinding]:
    """Find version-control or source risks without exposing their contents."""
    tracked = is_tracked or (lambda path: _is_git_tracked(root, path))
    findings: list[SecurityFinding] = []
    dotenv = root / ".env"
    if dotenv.is_file() and tracked(dotenv):
        findings.append(SecurityFinding("tracked_dotenv", "warning", ".env", 1, ".env is tracked by Git"))
    for path in iter_files(root, _SOURCE_EXTENSIONS):
        text = read_text(path)
        if not text:
            continue
        for match in _SECRET_LITERAL.finditer(text):
            findings.append(SecurityFinding(
                "hardcoded_secret", "warning", path.relative_to(root).as_posix(),
                text.count("\n", 0, match.start()) + 1,
                "Secret-like literal is present in source; move it to a secret manager or runtime configuration.",
            ))
    return findings


def _is_git_tracked(root: Path, path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", path.relative_to(root).as_posix()],
            cwd=root, capture_output=True, text=True, timeout=5, check=False,
        )
    except OSError:
        return False
    return result.returncode == 0
