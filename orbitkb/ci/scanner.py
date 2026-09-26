"""Narrow GitHub Actions command extraction without executing workflow content."""
from __future__ import annotations

import re
from pathlib import Path

from orbitkb.discovery.scan_helpers import SKIP_DIRS

_RUN = re.compile(r"^\s*(?:-\s*)?run:\s*(?P<command>\S.*)\s*$")
_SECRET_REFERENCE = re.compile(r"\$\{\{\s*secrets\.|\b(?:password|token|api[_-]?key|secret)\b", re.IGNORECASE)
_KINDS = (
    ("migration", re.compile(r"\b(?:migrate|migration|flyway|liquibase)\b", re.IGNORECASE)),
    ("client_generation", re.compile(r"\b(?:generate|codegen|protoc)\b", re.IGNORECASE)),
    ("test", re.compile(r"\b(?:test|pytest|jest|vitest)\b", re.IGNORECASE)),
    ("build", re.compile(r"\b(?:build|compile|package)\b", re.IGNORECASE)),
)


def scan_github_actions_commands(repository_root: Path) -> list[dict]:
    """Return classified literal validation commands from GitHub Actions workflows.

    Multiline, expression-based and secret-bearing commands are intentionally omitted:
    they cannot be represented safely as one source-proven command for an agent.
    """
    workflows_root = repository_root / ".github" / "workflows"
    if not workflows_root.is_dir():
        return []
    commands: list[dict] = []
    for workflow in sorted(path for suffix in ("*.yml", "*.yaml") for path in workflows_root.rglob(suffix)):
        if any(part in SKIP_DIRS for part in workflow.relative_to(repository_root).parts):
            continue
        commands.extend(_commands_in_workflow(workflow, repository_root))
    return commands


def _commands_in_workflow(workflow: Path, repository_root: Path) -> list[dict]:
    commands: list[dict] = []
    relative_path = workflow.relative_to(repository_root).as_posix()
    for line_number, line in enumerate(workflow.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
        match = _RUN.match(line)
        if match is None:
            continue
        command = match.group("command").strip().strip("\"'")
        kind = _command_kind(command)
        if kind is None or command in {"|", ">", "|-", ">-"} or "${{" in command or _SECRET_REFERENCE.search(command):
            continue
        commands.append({
            "workflow_path": relative_path,
            "kind": kind,
            "command": command,
            "evidence": {"file": relative_path, "start_line": line_number, "end_line": line_number},
        })
    return commands


def _command_kind(command: str) -> str | None:
    for kind, pattern in _KINDS:
        if pattern.search(command):
            return kind
    return None
