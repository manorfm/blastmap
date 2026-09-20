"""Unit tests for CodexBackend.generate(): subprocess.run is monkeypatched so this
never shells out to a real `codex` CLI, and the -o output file it reads is written
directly by the fake run(). Covers the new best-effort usage extraction from the
`codex exec --json` JSONL event stream on stdout — a shape this codebase has never
verified against a real invocation, so it must degrade to None instead of raising
when no usage-carrying event is found.
"""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from orbitkb.generation.backend_base import GenerationError
from orbitkb.generation.codex_backend import CodexBackend

SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}}}


def _patch_codex_run(monkeypatch, stdout_lines: list[str], structured: dict, returncode: int = 0):
    def run(cmd, cwd, capture_output, text, timeout, stdin, env):
        # cmd = [..., "-o", "<output_path>", ...]
        output_path = Path(cmd[cmd.index("-o") + 1])
        output_path.write_text(json.dumps(structured), encoding="utf-8")
        return SimpleNamespace(returncode=returncode, stdout="\n".join(stdout_lines), stderr="")

    monkeypatch.setattr(subprocess, "run", run)


def test_generate_extracts_structured_output_and_usage_from_json_stream(monkeypatch, tmp_path: Path):
    events = [
        json.dumps({"type": "turn", "msg": "thinking"}),
        json.dumps({"type": "token_count", "usage": {"input_tokens": 200, "output_tokens": 40}, "total_cost_usd": 0.004}),
    ]
    _patch_codex_run(monkeypatch, events, {"summary": "ok"})

    outcome = CodexBackend().generate("prompt", SCHEMA, tmp_path)

    assert outcome.structured == {"summary": "ok"}
    assert outcome.usage.input_tokens == 200
    assert outcome.usage.output_tokens == 40
    assert outcome.usage.cost_usd == 0.004


def test_generate_leaves_usage_none_when_no_event_carries_it(monkeypatch, tmp_path: Path):
    events = [json.dumps({"type": "turn", "msg": "thinking"})]
    _patch_codex_run(monkeypatch, events, {"summary": "ok"})

    outcome = CodexBackend().generate("prompt", SCHEMA, tmp_path)

    assert outcome.structured == {"summary": "ok"}
    assert outcome.usage.input_tokens is None
    assert outcome.usage.cost_usd is None


def test_generate_raises_on_invalid_output_file_json(monkeypatch, tmp_path: Path):
    def run(cmd, cwd, capture_output, text, timeout, stdin, env):
        output_path = Path(cmd[cmd.index("-o") + 1])
        output_path.write_text("not json", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(GenerationError):
        CodexBackend().generate("prompt", SCHEMA, tmp_path)
