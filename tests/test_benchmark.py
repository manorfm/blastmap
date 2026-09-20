"""CI-safe regression guard: does candidate retrieval (no LLM involved) still surface
every benchmark task's expected services? See benchmark/__init__.py and this
project's README for why this measures recall only, not end-to-end precision.
"""
from pathlib import Path

from benchmark.runner import run_retrieval_recall
from benchmark.tasks import TASKS, BenchmarkTask

from orbitkb.db.connection import open_db
from orbitkb.db.repositories import embeddings as embeddings_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.generation.retrieval import FallbackRetrieval, KeywordGraphRetrieval, SemanticRetrieval


def test_every_benchmark_task_is_fully_recalled(tmp_path: Path):
    report = run_retrieval_recall(TASKS, tmp_path)

    failing = [r for r in report.results if r.missing]
    assert not failing, f"retrieval regression: {[(r.task_id, r.missing) for r in failing]}"
    assert report.aggregate_recall == 1.0


def test_report_has_one_result_per_task(tmp_path: Path):
    report = run_retrieval_recall(TASKS, tmp_path)

    assert {r.task_id for r in report.results} == {t.id for t in TASKS}


def test_reduction_ratio_shows_candidates_are_a_minority_of_the_system(tmp_path: Path):
    report = run_retrieval_recall(TASKS, tmp_path)

    for r in report.results:
        assert r.total_services > 0
        # A task whose graph expansion reaches every service in a small, densely
        # connected fixture can legitimately hit 0 reduction — that's an honest
        # property of a tiny fixture, not a retrieval bug. The aggregate below is
        # the real signal: across a realistic task mix, most of the system stays
        # out of scope.
        assert 0.0 <= r.reduction_ratio < 1.0
    assert 0.0 < report.aggregate_reduction_ratio < 1.0


class FakeEmbeddingBackend:
    """Deterministic stand-in for FastEmbedBackend — see test_retrieval.py."""

    model_name = "fake-embedding-model"

    def __init__(self, vectors_by_text: dict[str, list[float]]):
        self._vectors_by_text = vectors_by_text

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vectors_by_text[t] for t in texts]


def _build_semantic_only_fixture(db_path: Path):
    """A single service whose description shares no vocabulary with the task below
    — keyword retrieval must find nothing here by construction; only a semantic
    fallback (cosine similarity on the pre-seeded embedding) can recall it."""
    conn = open_db(db_path)
    service_id = services_repo.ensure_service(conn, "ledger-service", "/tmp/ledger", "python")
    services_repo.update_service_overview(conn, service_id, "Maintains the double-entry financial ledger.", "L")
    embeddings_repo.upsert_service_embedding(conn, service_id, "fake-embedding-model", [1.0, 0.0])
    return conn


SEMANTIC_ONLY_TASK = BenchmarkTask(
    id="semantic-only-no-keyword-overlap",
    description="reconcile monetary records across accounts",
    build_fixture=_build_semantic_only_fixture,
    expected_services={"ledger-service"},
)


def test_semantic_fallback_recalls_a_task_that_defeats_keyword_matching(tmp_path: Path):
    """The main TASKS/run_retrieval_recall benchmark above is deliberately
    keyword-only (zero LLM/embedding cost, see benchmark/__init__.py) — this is the
    separate, narrower proof that the semantic fallback (generation/retrieval.py)
    actually recalls what plain keyword retrieval structurally cannot, using a fake
    embedding backend so it stays free of any real model/network dependency in CI.
    """
    embedding_backend = FakeEmbeddingBackend({SEMANTIC_ONLY_TASK.description: [1.0, 0.0]})
    retrieval = FallbackRetrieval(KeywordGraphRetrieval(), SemanticRetrieval(embedding_backend))

    keyword_only_report = run_retrieval_recall([SEMANTIC_ONLY_TASK], tmp_path / "keyword-only")
    assert keyword_only_report.aggregate_recall == 0.0  # confirms the task really does defeat keyword matching

    fallback_report = run_retrieval_recall([SEMANTIC_ONLY_TASK], tmp_path / "with-fallback", retrieval=retrieval)
    assert fallback_report.aggregate_recall == 1.0
