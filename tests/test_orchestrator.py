"""Exercises the real indexing pipeline (discovery -> prompt rendering -> backend ->
schema validation -> SQLite writes -> incremental hash-based skip) against the
project's own verify/sample_project fixture, faking only the LLM call itself so the
suite stays deterministic and never shells out to a real `claude`/`codex` CLI.
"""
from pathlib import Path

import pytest

from context_insight.db.connection import open_db
from context_insight.db.repositories import apis as apis_repo
from context_insight.db.repositories import repositories as repositories_repo
from context_insight.db.repositories import services as services_repo
from context_insight.discovery.registry import detector_for
from context_insight.discovery.walker import discover_services
from context_insight.generation.backend_base import GenerationError
from context_insight.generation.orchestrator import DiscoveryError, index_path, index_service

SAMPLE_ROOT = Path(__file__).resolve().parent.parent / "verify" / "sample_project"


class FakeOrchestratorBackend:
    """Returns a canned, schema-shaped response per unit kind, detected from the
    schema's own top-level property names (the same schemas orchestrator.py loads).
    Optionally fails a chosen unit kind, to exercise failure isolation.
    """

    name = "fake"

    def __init__(self, fail_kind: str | None = None):
        self.fail_kind = fail_kind
        self.calls = 0

    def _kind(self, schema: dict) -> str:
        props = schema.get("properties", {})
        if "short_desc" in props:
            return "service_overview"
        if "entities" in props:
            return "persistence"
        if "messages" in props:
            return "messaging"
        return "api_detail"

    def generate(self, prompt: str, schema: dict, cwd: Path) -> dict:
        self.calls += 1
        kind = self._kind(schema)
        if kind == self.fail_kind:
            raise GenerationError("simulated failure")
        if kind == "service_overview":
            return {"short_desc": "Fake short description.", "long_desc": "Fake long description."}
        if kind == "persistence":
            return {"entities": [{"name": "fake_table", "kind": "sql_table", "fields": [{"field": "id", "type_desc": "string"}]}]}
        if kind == "messaging":
            return {"messages": [{"direction": "publishes", "channel": "fake_channel", "shape": [], "description": "fake"}]}
        return {
            "summary": "Fake summary.",
            "description": "Fake description.",
            "response_shape": [{"field": "id", "type_desc": "string"}],
            "request_shape": [{"field": "amount", "type_desc": "number", "required": True}],
            "calls": [{
                "to_service_name": "payments-service", "call_kind": "http", "reason": "fake reason",
                "data_needed": ["amount"], "purpose_kind": "data_fetch", "confidence": 0.8, "target_kind": "internal",
            }],
            "validations": [{"kind": "authorization", "description": "fake auth rule"}],
        }


def test_index_path_indexes_all_three_sample_services(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    backend = FakeOrchestratorBackend()

    results = index_path(conn, SAMPLE_ROOT, backend)

    names = {r.service_name for r in results}
    assert names == {"orders-service", "payments-service", "inventory-service"}
    assert all(r.status == "ok" for r in results)
    assert all(r.llm_calls > 0 for r in results)

    services = {s["name"] for s in services_repo.list_services(conn)}
    assert services == names

    orders = services_repo.get_service_by_name(conn, "orders-service")
    assert orders["short_desc"] == "Fake short description."
    apis = apis_repo.list_apis(conn, orders["id"])
    assert any(a["method"] == "POST" and a["path"] == "/orders" for a in apis)


def test_repository_is_created_and_linked_to_all_services(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    index_path(conn, SAMPLE_ROOT, FakeOrchestratorBackend())

    repos = repositories_repo.list_repositories(conn)
    assert len(repos) == 1
    assert repos[0]["root_path"] == str(SAMPLE_ROOT.resolve())

    for svc in services_repo.list_services(conn):
        row = services_repo.get_service_by_name(conn, svc["name"])
        assert row["repository_id"] == repos[0]["id"]


def test_reindexing_unchanged_files_skips_generation(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    backend = FakeOrchestratorBackend()
    index_path(conn, SAMPLE_ROOT, backend)
    calls_after_first_run = backend.calls

    second_backend = FakeOrchestratorBackend()
    results = index_path(conn, SAMPLE_ROOT, second_backend)

    assert second_backend.calls == 0  # nothing changed, every unit skipped
    assert all(r.status == "ok" for r in results)
    assert all(r.llm_calls == 0 for r in results)
    assert calls_after_first_run > 0  # sanity: the first run did do real work


def test_force_reindex_regenerates_everything(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    index_path(conn, SAMPLE_ROOT, FakeOrchestratorBackend())

    backend = FakeOrchestratorBackend()
    results = index_path(conn, SAMPLE_ROOT, backend, force=True)

    assert backend.calls > 0
    assert all(r.llm_calls > 0 for r in results)


def test_generation_failure_is_isolated_per_unit(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    backend = FakeOrchestratorBackend(fail_kind="service_overview")
    failures_root = tmp_path / "failures"

    results = index_path(conn, SAMPLE_ROOT, backend, progress=None)
    # can't pass failures_root through index_path; call index_service directly instead
    conn2 = open_db(tmp_path / "test2.db")
    candidates = discover_services(SAMPLE_ROOT)
    orders = next(c for c in candidates if c.name == "orders-service")
    result = index_service(conn2, orders.name, orders.path, orders.detector, backend, failures_root=failures_root)

    assert result.status == "partial"
    # the API unit still succeeded even though overview failed for this service
    orders_row = services_repo.get_service_by_name(conn2, "orders-service")
    assert orders_row["short_desc"] is None  # overview failed, never written
    apis = apis_repo.list_apis(conn2, orders_row["id"])
    assert len(apis) == 1  # api_detail unit succeeded independently
    assert failures_root.exists()
    assert list(failures_root.glob("*.txt"))

    # results from the first index_path call are still meaningful: overview failed
    # for every service (since fail_kind applies globally), so every one is partial
    assert all(r.status == "partial" for r in results)


def test_index_path_raises_discovery_error_on_empty_directory(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    empty_dir = tmp_path / "nothing_here"
    empty_dir.mkdir()

    with pytest.raises(DiscoveryError):
        index_path(conn, empty_dir, FakeOrchestratorBackend())


def test_index_path_service_override_requires_single_candidate(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    with pytest.raises(DiscoveryError):
        index_path(conn, SAMPLE_ROOT, FakeOrchestratorBackend(), service_override="custom-name")


def test_index_service_directly_with_service_override_style_path(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    single_service_root = SAMPLE_ROOT / "orders-service"
    detector = detector_for(single_service_root)
    assert detector is not None

    result = index_service(conn, "custom-orders-name", single_service_root, detector, FakeOrchestratorBackend())

    assert result.status == "ok"
    assert services_repo.get_service_by_name(conn, "custom-orders-name") is not None
