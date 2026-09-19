"""Shared LLM invocation harness: prompt/schema loading, retry-with-validation, and
failure logging. Used by both the indexing orchestrator and change_surface — the one
place that knows how to turn a prompt + schema into a validated dict via an
LLMBackend, so neither caller re-implements retry/validation logic.
"""
from __future__ import annotations

import json
import logging
import time
from functools import lru_cache
from importlib import resources
from pathlib import Path
from string import Template

import jsonschema

from orbitkb.generation.backend_base import GenerationError, GenerationOutcome, LLMBackend, LLMUsage

logger = logging.getLogger(__name__)

PROMPTS_PKG = "orbitkb.generation.prompts"
SCHEMAS_PKG = "orbitkb.generation.schemas"


@lru_cache(maxsize=None)
def load_prompt(name: str) -> Template:
    text = resources.files(PROMPTS_PKG).joinpath(f"{name}.md").read_text(encoding="utf-8")
    return Template(text)


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict:
    text = resources.files(SCHEMAS_PKG).joinpath(f"{name}.schema.json").read_text(encoding="utf-8")
    return json.loads(text)


def generate_with_retry(
    backend: LLMBackend, prompt: str, schema: dict, cwd: Path, failures_dir: Path, label: str
) -> GenerationOutcome | None:
    last_error: Exception | None = None
    current_prompt = prompt
    total_usage = LLMUsage()
    for _attempt in range(2):
        try:
            outcome = backend.generate(current_prompt, schema, cwd)
            total_usage = total_usage + outcome.usage  # a retried call is still a billed call
            jsonschema.validate(outcome.structured, schema)
            return GenerationOutcome(structured=outcome.structured, usage=total_usage)
        except (GenerationError, jsonschema.ValidationError) as exc:
            last_error = exc
            current_prompt = (
                f"{prompt}\n\nYour previous response was invalid: {exc}. "
                "Return ONLY valid JSON matching the schema, no prose, no markdown fences."
            )

    failures_dir.mkdir(parents=True, exist_ok=True)
    safe_label = label.replace("/", "_")
    fail_file = failures_dir / f"{safe_label}-{int(time.time())}.txt"
    fail_file.write_text(f"Prompt:\n{prompt}\n\nLast error:\n{last_error}", encoding="utf-8")
    logger.warning("generation failed for %s: %s (see %s)", label, last_error, fail_file)
    return None
