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
# A cloud credential's own literal FORMAT is the proof here, not a nearby
# variable name — closes the gap _SECRET_LITERAL leaves open for
# `accessKeyId: "AKIA..."`/`AccountKey=...`, where the key name itself
# contains none of _SECRET_LITERAL's keywords. Vendor-documented formats:
# AWS access key IDs (long-term AKIA.../temporary ASIA... both 16 chars
# after the prefix), Azure Storage connection strings, and a PEM private key
# block (GCP service-account JSON keys embed one, though the marker itself
# isn't GCP-specific).
_CLOUD_CREDENTIAL_LITERAL = re.compile(
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
    r"|\bAccountKey=[A-Za-z0-9+/=]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
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
        for match in _CLOUD_CREDENTIAL_LITERAL.finditer(text):
            findings.append(SecurityFinding(
                # security_findings.severity's own scale tops out at 'error'
                # (no 'critical' tier the way architecture_findings has) --
                # this is that table's own highest severity, not a downgrade.
                "cloud_credential_literal", "error", path.relative_to(root).as_posix(),
                text.count("\n", 0, match.start()) + 1,
                "Cloud provider credential literal is present in source; rotate it and move to a secret manager immediately.",
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
