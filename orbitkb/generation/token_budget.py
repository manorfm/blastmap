"""Honest, local measurements for bounded JSON MCP responses."""
from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from typing import Protocol


class _Encoding(Protocol):
    def encode(self, text: str) -> list[int]: ...


@dataclass(frozen=True)
class TokenMeasurement:
    """A token total together with the method that produced it."""

    tokens: int
    method: str


def measure_json_tokens(payload: object) -> TokenMeasurement:
    """Measure compact JSON with o200k when installed, otherwise label an estimate."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    encoding = _load_o200k_encoding()
    if encoding is None:
        return TokenMeasurement((len(encoded) + 3) // 4, "byte_estimate")
    return TokenMeasurement(len(encoding.encode(encoded.decode("utf-8"))), "tiktoken:o200k_base")


def _load_o200k_encoding() -> _Encoding | None:
    """Load the optional tokenizer without making it a base runtime dependency."""
    try:
        tiktoken = importlib.import_module("tiktoken")
    except ModuleNotFoundError:
        return None
    return tiktoken.get_encoding("o200k_base")
