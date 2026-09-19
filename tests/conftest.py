import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def no_real_semantic_backend(monkeypatch):
    """The suite must never depend on the optional `semantic` extra being installed
    or on downloading a real embedding model over the network — same discipline the
    project already applies to LLMBackend (always faked in tests). Patches
    FastEmbedBackend itself (simulating "not installed") rather than
    try_create_default_backend() directly, so that function's own real logic still
    runs everywhere, including in its dedicated unit tests. Individual tests that
    want to exercise the semantic-retrieval fallback path override this back to a
    fake EmbeddingBackend themselves (see test_change_surface.py/test_retrieval.py)."""

    def _unavailable(*args, **kwargs):
        raise ImportError("fastembed not installed (test suite default)")

    monkeypatch.setattr("impactmesh.generation.embeddings.FastEmbedBackend", _unavailable, raising=False)
