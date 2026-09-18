"""Exercises the real indexing pipeline (discovery -> prompt rendering -> backend ->
schema validation -> SQLite writes -> incremental hash-based skip) against the
project's own verify/sample_project fixture, faking only the LLM call itself so the
suite stays deterministic and never shells out to a real `claude`/`codex` CLI.
"""
from pathlib import Path

import pytest

from context_insight.db.connection import open_db
from context_insight.db.repositories import apis as apis_repo
from context_insight.db.repositories import components as components_repo
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
        if set(props) == {"summary"}:
            return "component"
        return "api_detail"

    def generate(self, prompt: str, schema: dict, cwd: Path) -> dict:
        self.calls += 1
        kind = self._kind(schema)
        if kind == self.fail_kind:
            raise GenerationError("simulated failure")
        if kind == "service_overview":
            return {"short_desc": "Fake short description.", "long_desc": "Fake long description."}
        if kind == "persistence":
            return {
                "entities": [
                    {"name": "fake_table", "kind": "sql_table", "engine": "postgres", "fields": [{"field": "id", "type_desc": "string"}]}
                ]
            }
        if kind == "messaging":
            return {"messages": [{"direction": "publishes", "channel": "fake_channel", "provider": "kafka", "shape": [], "description": "fake"}]}
        if kind == "component":
            return {"summary": "Fake component summary."}
        return {
            "summary": "Fake summary.",
            "description": "Fake description.",
            "response_shape": [{"field": "id", "type_desc": "string"}],
            "request_shape": [{"field": "amount", "type_desc": "number", "required": True}],
            "calls": [{
                "to_service_name": "payments-service", "call_kind": "http", "reason": "fake reason",
                "data_needed": ["amount"], "purpose_kind": "data_fetch", "confidence": 0.8, "target_kind": "internal",
                "resource_type": "not_applicable",
            }],
            "validations": [{"kind": "authorization", "description": "fake auth rule"}],
        }


class RecordingOrchestratorBackend(FakeOrchestratorBackend):
    """Same canned responses as FakeOrchestratorBackend, but also keeps every prompt it
    was given, keyed by unit kind — lets a test assert not just what got persisted but
    what the LLM actually saw for a given unit (e.g. that the overview prompt really
    includes the component summaries composed below it, not just a coincidence)."""

    def __init__(self, fail_kind: str | None = None):
        super().__init__(fail_kind=fail_kind)
        self.prompts_by_kind: dict[str, list[str]] = {}

    def generate(self, prompt: str, schema: dict, cwd: Path) -> dict:
        self.prompts_by_kind.setdefault(self._kind(schema), []).append(prompt)
        return super().generate(prompt, schema, cwd)


def test_message_provider_is_persisted_from_the_llm_result(tmp_path: Path):
    from context_insight.db.repositories import messages as messages_repo

    conn = open_db(tmp_path / "test.db")
    index_path(conn, SAMPLE_ROOT, FakeOrchestratorBackend())

    orders = services_repo.get_service_by_name(conn, "orders-service")
    messages = messages_repo.list_messages(conn, orders["id"])
    assert messages[0]["provider"] == "kafka"


def test_messaging_prompt_includes_provider_hints_and_config_evidence(tmp_path: Path):
    backend = RecordingOrchestratorBackend()
    conn = open_db(tmp_path / "test.db")
    index_path(conn, SAMPLE_ROOT, backend)

    messaging_prompts = backend.prompts_by_kind["messaging"]
    assert any("Best-effort provider guesses" in p for p in messaging_prompts)
    assert any("kafka" in p for p in messaging_prompts)


def test_components_are_synthesized_from_endpoint_summaries_not_raw_code(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    index_path(conn, SAMPLE_ROOT, FakeOrchestratorBackend())

    orders = services_repo.get_service_by_name(conn, "orders-service")
    components = components_repo.list_components(conn, orders["id"])
    assert len(components) == 1
    assert components[0]["name"] == "main"  # no class wraps the endpoint in this fixture
    assert components[0]["summary"] == "Fake component summary."


def test_overview_prompt_is_composed_from_the_components_summary(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    backend = RecordingOrchestratorBackend()
    index_path(conn, SAMPLE_ROOT, backend)

    overview_prompts = backend.prompts_by_kind["service_overview"]
    assert any("Fake component summary." in p for p in overview_prompts)


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
