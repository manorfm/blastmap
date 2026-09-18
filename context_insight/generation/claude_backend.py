from __future__ import annotations

import json
import subprocess
from pathlib import Path

from context_insight.generation.backend_base import GenerationError

TIMEOUT_SECONDS = 180


class ClaudeBackend:
    """Headless Claude Code CLI backend.

    Uses the normal OAuth/subscription session by default (no --bare, no API key),
    so generation cost counts against the user's existing Claude Code plan rather
    than metered API billing. Pass bare=True to opt into ANTHROPIC_API_KEY billing
    instead (useful for CI where no interactive login is available).
    """

    name = "claude"

    def __init__(self, model: str | None = None, bare: bool = False):
        self.model = model
        self.bare = bare

    def generate(self, prompt: str, schema: dict, cwd: Path) -> dict:
        cmd = [
            "claude", "-p", prompt,
            "--output-format", "json",
            "--json-schema", json.dumps(schema),
            "--allowedTools", "",
            "--permission-mode", "dontAsk",
        ]
        if self.model:
            cmd += ["--model", self.model]
        if self.bare:
            cmd.append("--bare")

        try:
            result = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise GenerationError(f"claude timed out after {TIMEOUT_SECONDS}s") from exc

        if result.returncode != 0:
            raise GenerationError(f"claude exited {result.returncode}: {result.stderr[-2000:]}")

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise GenerationError(f"claude returned non-JSON stdout: {exc}\n{result.stdout[-2000:]}") from exc

        if payload.get("is_error"):
            raise GenerationError(f"claude reported an error: {payload.get('result')}")

        structured = payload.get("structured_output")
        if structured is None:
            raise GenerationError(f"claude response had no structured_output: {result.stdout[-2000:]}")
        return structured
