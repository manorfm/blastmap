from __future__ import annotations

from pathlib import Path
from typing import Protocol


class GenerationError(Exception):
    """Raised when a backend fails to produce valid structured output."""


class LLMBackend(Protocol):
    name: str

    def generate(self, prompt: str, schema: dict, cwd: Path) -> dict:
        """Run the prompt through the model and return parsed JSON matching schema.

        Implementations must not allow the model to use tools/shell access — all
        context is pasted into the prompt already, so the model only needs to reason
        over text and return structured JSON.
        """
        ...
