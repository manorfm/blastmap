"""TDD coverage for KeywordGraphRetrieval, the candidate retrieval strategy
extracted out of generation/change_surface.py (Strategy pattern: a future retrieval
approach can implement CandidateRetrieval without touching change_surface.py)."""
from pathlib import Path

from orbitkb.db.connection import open_db
from orbitkb.db.repositories import apis as apis_repo
from orbitkb.db.repositories import embeddings as embeddings_repo
from orbitkb.db.repositories import messages as messages_repo
from orbitkb.db.repositories import search as search_repo
from orbitkb.db.repositories import service_calls as service_calls_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.generation.retrieval import (
    FallbackRetrieval,
    HybridRetrieval,
    KeywordGraphRetrieval,
    SemanticRetrieval,
)


def _seed_db(db_path: Path):
    conn = open_db(db_path)
    checkout_id = services_repo.ensure_service(conn, "checkout-service", "/tmp/checkout", "python")
    payments_id = services_repo.ensure_service(conn, "payments-service", "/tmp/payments", "node-ts")
    order_id = services_repo.ensure_service(conn, "order-service", "/tmp/order", "python")
    notif_id = services_repo.ensure_service(conn, "notification-service", "/tmp/notif", "python")
    services_repo.update_service_overview(conn, checkout_id, "Owns the checkout entry point.", "L")
    services_repo.update_service_overview(conn, payments_id, "Owns payment authorization.", "L")
    services_repo.update_service_overview(conn, order_id, "Creates orders.", "L")
    services_repo.update_service_overview(conn, notif_id, "Sends notification emails.", "L")

    api_id = apis_repo.upsert_api(conn, checkout_id, "POST", "/checkout", "starts checkout", "d", [], [])
    service_calls_repo.replace_calls_for_api(
        conn, checkout_id, api_id,
        [{"to_service_name": "payments-service", "call_kind": "http", "reason": "authorize payment",
          "data_needed": [], "purpose_kind": "data_fetch", "confidence": 0.9}],
        [],
    )
    service_calls_repo.reconcile_service_call_targets(conn)

    messages_repo.replace_messages(
        conn, payments_id, [{"direction": "publishes", "channel": "payment_authorized", "shape_json": [], "description": "d"}], [],
    )
    messages_repo.replace_messages(
        conn, order_id, [{"direction": "consumes", "channel": "payment_authorized", "shape_json": [], "description": "d"}], [],
    )
    search_repo.rebuild_search_index(conn)
    return conn


def test_keyword_match_seeds_and_expands_through_calls_and_message_links(tmp_path: Path):
    conn = _seed_db(tmp_path / "retrieval.db")
    retrieval = KeywordGraphRetrieval()

    candidates = retrieval.candidates(conn, "checkout payment flow", hint_services=None, max_candidates=10)

    # checkout-service matches the keywords directly; payments-service is reached by
    # an outbound call, order-service by a publish->consume message link.
    assert {"checkout-service", "payments-service", "order-service"}.issubset(set(candidates))


def test_hint_services_are_seeded_even_without_a_keyword_match(tmp_path: Path):
    conn = _seed_db(tmp_path / "retrieval2.db")
    retrieval = KeywordGraphRetrieval()

    candidates = retrieval.candidates(conn, "xyz totally unrelated", hint_services=["notification-service"], max_candidates=10)

    assert "notification-service" in candidates


def test_no_seeds_and_no_hints_returns_empty(tmp_path: Path):
    conn = _seed_db(tmp_path / "retrieval3.db")
    retrieval = KeywordGraphRetrieval()

    candidates = retrieval.candidates(conn, "xyz totally unrelated", hint_services=None, max_candidates=10)

    assert candidates == []


def test_seeding_issues_one_batched_search_call_not_one_per_keyword(tmp_path: Path, monkeypatch):
    conn = _seed_db(tmp_path / "retrieval5.db")
    retrieval = KeywordGraphRetrieval()
    calls = []
    real_search = search_repo.search

    def spy(conn_, query, limit=20):
        calls.append(query)
        return real_search(conn_, query, limit=limit)

    monkeypatch.setattr(search_repo, "search", spy)

    # Three keywords, each matching a different service by itself.
    candidates = retrieval.candidates(conn, "checkout payment orders", hint_services=None, max_candidates=10)

    assert len(calls) == 1  # batched into a single FTS5 query, not one per keyword
    assert {"checkout-service", "payments-service", "order-service"}.issubset(set(candidates))


def test_candidates_are_capped_at_max_candidates(tmp_path: Path):
    conn = _seed_db(tmp_path / "retrieval4.db")
    retrieval = KeywordGraphRetrieval()

    candidates = retrieval.candidates(
        conn, "checkout", hint_services=["payments-service", "order-service", "notification-service"], max_candidates=2
    )

    assert len(candidates) <= 2


class FakeEmbeddingBackend:
    """Deterministic stand-in for FastEmbedBackend: a text's 'vector' is a
    hand-picked 2D point, so cosine similarity between fixture texts is
    predictable without downloading or running a real model."""

    model_name = "fake-embedding-model"

    def __init__(self, vectors_by_text: dict[str, list[float]]):
        self._vectors_by_text = vectors_by_text
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [self._vectors_by_text[t] for t in texts]


def _seed_embeddings(conn, vectors_by_service: dict[str, list[float]]) -> None:
    for name, vector in vectors_by_service.items():
        row = services_repo.get_service_by_name(conn, name)
        embeddings_repo.upsert_service_embedding(conn, row["id"], "fake-embedding-model", vector)


def test_semantic_retrieval_ranks_services_by_cosine_similarity_to_the_task(tmp_path: Path):
    conn = _seed_db(tmp_path / "semantic1.db")
    _seed_embeddings(conn, {
        "checkout-service": [1.0, 0.0],
        "payments-service": [0.9, 0.1],
        "order-service": [0.0, 1.0],
        "notification-service": [-1.0, 0.0],
    })
    backend = FakeEmbeddingBackend({"a task about checkout and payments": [1.0, 0.0]})
    retrieval = SemanticRetrieval(backend)

    candidates = retrieval.candidates(conn, "a task about checkout and payments", hint_services=None, max_candidates=10)

    assert candidates[0] == "checkout-service"
    assert "payments-service" in candidates
    assert "notification-service" not in candidates  # below the similarity floor


def test_semantic_retrieval_returns_hint_services_even_with_no_embeddings_indexed(tmp_path: Path):
    conn = _seed_db(tmp_path / "semantic2.db")
    backend = FakeEmbeddingBackend({"xyz": [0.0, 0.0]})
    retrieval = SemanticRetrieval(backend)

    candidates = retrieval.candidates(conn, "xyz", hint_services=["notification-service"], max_candidates=10)

    assert candidates == ["notification-service"]


def test_fallback_retrieval_only_calls_secondary_when_primary_finds_nothing(tmp_path: Path):
    conn = _seed_db(tmp_path / "fallback1.db")
    _seed_embeddings(conn, {"notification-service": [1.0, 0.0]})
    backend = FakeEmbeddingBackend({"xyz totally unrelated": [1.0, 0.0]})
    fallback = FallbackRetrieval(KeywordGraphRetrieval(), SemanticRetrieval(backend))

    # A keyword match exists ("checkout"), so the semantic backend must never be called.
    candidates = fallback.candidates(conn, "checkout payment flow", hint_services=None, max_candidates=10)

    assert backend.calls == 0
    assert "checkout-service" in candidates


def test_fallback_retrieval_uses_semantic_when_keyword_retrieval_finds_nothing(tmp_path: Path):
    conn = _seed_db(tmp_path / "fallback2.db")
    _seed_embeddings(conn, {"notification-service": [1.0, 0.0]})
    backend = FakeEmbeddingBackend({"xyz totally unrelated": [1.0, 0.0]})
    fallback = FallbackRetrieval(KeywordGraphRetrieval(), SemanticRetrieval(backend))

    candidates = fallback.candidates(conn, "xyz totally unrelated", hint_services=None, max_candidates=10)

    assert backend.calls == 1
    assert candidates == ["notification-service"]


def test_hybrid_retrieval_keeps_a_semantic_candidate_when_keywords_also_match(tmp_path: Path):
    conn = _seed_db(tmp_path / "hybrid.db")
    _seed_embeddings(conn, {"notification-service": [1.0, 0.0]})
    backend = FakeEmbeddingBackend({"checkout payment flow": [1.0, 0.0]})
    retrieval = HybridRetrieval(KeywordGraphRetrieval(), SemanticRetrieval(backend))

    candidates = retrieval.candidates(conn, "checkout payment flow", hint_services=None, max_candidates=4)

    assert backend.calls == 1
    assert "checkout-service" in candidates
    assert "notification-service" in candidates
    assert len(candidates) <= 4
