from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from context_insight.generation.backend_base import GenerationError

TIMEOUT_SECONDS = 180


class CodexBackend:
    """Headless Codex CLI backend.

    Uses the default ChatGPT OAuth session (`codex login`) by default, so generation
    counts against the user's Codex subscription rather than metered API billing.
    Pass api_key=True to opt into CODEX_API_KEY billing instead.
    """

    name = "codex"

    def __init__(self, model: str | None = None, api_key: bool = False):
        self.model = model
        self.api_key = api_key

    def generate(self, prompt: str, schema: dict, cwd: Path) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            schema_path = Path(tmp) / "schema.json"
            output_path = Path(tmp) / "output.txt"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")

            cmd = [
                "codex", "exec",
                "--json",
                "--output-schema", str(schema_path),
                "-o", str(output_path),
                "-s", "read-only",
                "-C", str(cwd),
                "--skip-git-repo-check",
            ]
            if self.model:
                cmd += ["-m", self.model]
            cmd.append(prompt)

            env = None
            if not self.api_key:
                import os
                env = {k: v for k, v in os.environ.items() if k != "CODEX_API_KEY"}

            try:
                result = subprocess.run(
                    cmd, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
                    stdin=subprocess.DEVNULL, env=env,
                )
            except subprocess.TimeoutExpired as exc:
                raise GenerationError(f"codex timed out after {TIMEOUT_SECONDS}s") from exc

            if result.returncode != 0:
                raise GenerationError(f"codex exited {result.returncode}: {result.stderr[-2000:]}")

            if not output_path.exists():
                raise GenerationError(f"codex produced no output file. stdout: {result.stdout[-2000:]}")

            raw = output_path.read_text(encoding="utf-8").strip()
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise GenerationError(f"codex final message was not valid JSON: {exc}\n{raw[-2000:]}") from exc
