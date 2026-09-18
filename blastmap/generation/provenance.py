"""Provenance: makes explicit whether a piece of information is a deterministically
extracted fact or an LLM-synthesized interpretation, formalizing a distinction the
schema already carries implicitly (service_calls.confidence is null for a purely
structural fact, set by the LLM otherwise)."""
from __future__ import annotations


def infer_provenance(confidence: float | None) -> dict:
    return {"source": "llm" if confidence is not None else "deterministic"}
