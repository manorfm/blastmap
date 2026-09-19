"""Local, zero-marginal-cost semantic embeddings: turns text into a vector so
find_change_surface can fall back to meaning-based candidate retrieval when FTS5
keyword matching finds nothing (see generation/retrieval.SemanticRetrieval and
db/repositories/embeddings.py). No paid API: FastEmbedBackend runs a small ONNX
model entirely on CPU via the optional `fastembed` package (`pip install
impactmesh[semantic]`) — its weights are downloaded once from Hugging Face on first
use and cached locally after that; every later call is fully offline.

The default install never requires fastembed: FastEmbedBackend only imports it
inside __init__, and try_create_default_backend() is the one place that decides
whether the optional extra is present, so every caller (orchestrator, change_surface)
shares the same ImportError-safe construction instead of duplicating a try/except.
"""
from __future__ import annotations

import math
from typing import Protocol

DEFAULT_MODEL_NAME = "BAAI/bge-small-en-v1.5"


class EmbeddingBackend(Protocol):
    model_name: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text, same order."""
        ...


class FastEmbedBackend:
    """Wraps fastembed.TextEmbedding. Constructing this without the `semantic`
    extra installed raises ImportError — callers that want to degrade gracefully
    should go through try_create_default_backend() instead of calling this
    directly.

    The actual model (a real ONNX load, possibly a first-time download) is built
    lazily on the first embed() call rather than in __init__: analyze_change_surface
    calls try_create_default_backend() on every run just to decide which retrieval
    strategy to use, and that decision must stay cheap on the common path where the
    keyword strategy already finds something and the embedding model is never
    actually needed.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL_NAME):
        import fastembed  # optional dependency; raises ImportError if not installed

        self.model_name = model_name
        self._fastembed = fastembed
        self._model = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            self._model = self._fastembed.TextEmbedding(model_name=self.model_name)
        return [vector.tolist() for vector in self._model.embed(texts)]


def try_create_default_backend() -> EmbeddingBackend | None:
    """The single place that knows how to degrade when `fastembed` isn't
    installed: returns None instead of raising, so every caller can treat
    semantic retrieval as a plain optional feature."""
    try:
        return FastEmbedBackend()
    except ImportError:
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
