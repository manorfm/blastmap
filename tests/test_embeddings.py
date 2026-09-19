"""Unit tests for generation.embeddings: cosine similarity, and
try_create_default_backend's ImportError-safe construction. FastEmbedBackend
itself is never exercised for real here (that would download a real ONNX model
over the network) — both branches of try_create_default_backend are covered by
monkeypatching the FastEmbedBackend class it calls, so the test suite stays fast
and deterministic regardless of whether the `semantic` extra happens to be
installed in a given environment."""
import pytest

import impactmesh.generation.embeddings as embeddings_module
from impactmesh.generation.embeddings import FastEmbedBackend, cosine_similarity


def test_cosine_similarity_of_identical_vectors_is_one():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_cosine_similarity_of_orthogonal_vectors_is_zero():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_similarity_of_opposite_vectors_is_negative_one():
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == -1.0


def test_cosine_similarity_handles_empty_vectors_without_raising():
    assert cosine_similarity([], []) == 0.0
    assert cosine_similarity([], [1.0]) == 0.0


def test_try_create_default_backend_returns_none_when_fastembed_is_unavailable(monkeypatch):
    def _raise(*args, **kwargs):
        raise ImportError("fastembed not installed")

    monkeypatch.setattr(embeddings_module, "FastEmbedBackend", _raise)

    assert embeddings_module.try_create_default_backend() is None


def test_fastembed_backend_defers_model_construction_until_embed_is_called(monkeypatch):
    """try_create_default_backend() runs on every analyze_change_surface call just to
    decide which retrieval strategy to use — it must stay cheap on the common path
    where keyword retrieval already finds something and the model is never actually
    needed (see FastEmbedBackend's docstring)."""
    pytest.importorskip("fastembed")
    numpy = pytest.importorskip("numpy")
    import fastembed as fastembed_module

    constructed: list[str] = []

    class SpyTextEmbedding:
        def __init__(self, model_name):
            constructed.append(model_name)

        def embed(self, texts):
            return [numpy.array([0.0]) for _ in texts]

    monkeypatch.setattr(fastembed_module, "TextEmbedding", SpyTextEmbedding)

    backend = FastEmbedBackend(model_name="test-model")
    assert constructed == []  # __init__ alone must never load the real model

    backend.embed(["hello"])
    assert constructed == ["test-model"]


def test_try_create_default_backend_returns_a_backend_when_available(monkeypatch):
    class _StubBackend:
        model_name = "stub-model"

        def embed(self, texts):
            return [[0.0] for _ in texts]

    monkeypatch.setattr(embeddings_module, "FastEmbedBackend", _StubBackend)

    backend = embeddings_module.try_create_default_backend()

    assert backend is not None
    assert backend.model_name == "stub-model"
