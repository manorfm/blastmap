"""TDD coverage for generation/architecture.py — deterministic, whole-graph structural
findings computed purely from already-seeded db.repositories.* facts, no LLM."""
from pathlib import Path

from context_insight.db.connection import open_db
from context_insight.db.repositories import apis as apis_repo
from context_insight.db.repositories import architecture as architecture_repo
from context_insight.db.repositories import persistence as persistence_repo
from context_insight.db.repositories import service_calls as service_calls_repo
from context_insight.db.repositories import services as services_repo
from context_insight.generation.architecture import (
    find_cycles,
    find_duplicate_external_integrations,
    find_fan_imbalance,
    find_shared_database,
    recompute_architecture_view,
)

EVIDENCE = [{"file": "main.py", "start_line": 1, "end_line": 5}]


def _call(conn, from_id, api_id, to_name, target_kind="internal"):
    service_calls_repo.replace_calls_for_api(
        conn, from_id, api_id,
        [{
            "to_service_name": to_name, "call_kind": "http", "reason": "r", "data_needed": [],
            "purpose_kind": "other", "confidence": 0.9, "target_kind": target_kind,
        }],
        EVIDENCE,
    )


def test_find_cycles_detects_a_two_service_cycle(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    a = services_repo.ensure_service(conn, "a-service", "/tmp/a", "python")
    b = services_repo.ensure_service(conn, "b-service", "/tmp/b", "python")
    a_api = apis_repo.upsert_api(conn, a, "GET", "/a", "s", "d", [], EVIDENCE)
    b_api = apis_repo.upsert_api(conn, b, "GET", "/b", "s", "d", [], EVIDENCE)
    _call(conn, a, a_api, "b-service")
    _call(conn, b, b_api, "a-service")
    service_calls_repo.reconcile_service_call_targets(conn)

    findings = find_cycles(conn)

    assert len(findings) == 1
    assert set(findings[0]["services"]) == {"a-service", "b-service"}
    assert findings[0]["kind"] == "cycle"


def test_find_cycles_ignores_a_simple_chain(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    a = services_repo.ensure_service(conn, "a-service", "/tmp/a", "python")
    b = services_repo.ensure_service(conn, "b-service", "/tmp/b", "python")
    services_repo.ensure_service(conn, "c-service", "/tmp/c", "python")
    a_api = apis_repo.upsert_api(conn, a, "GET", "/a", "s", "d", [], EVIDENCE)
    b_api = apis_repo.upsert_api(conn, b, "GET", "/b", "s", "d", [], EVIDENCE)
    _call(conn, a, a_api, "b-service")
    _call(conn, b, b_api, "c-service")
    service_calls_repo.reconcile_service_call_targets(conn)

    assert find_cycles(conn) == []


def test_find_fan_imbalance_flags_high_fan_in(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    services_repo.ensure_service(conn, "hub-service", "/tmp/hub", "python")
    for i in range(4):
        caller = services_repo.ensure_service(conn, f"caller-{i}-service", f"/tmp/c{i}", "python")
        api_id = apis_repo.upsert_api(conn, caller, "GET", "/x", "s", "d", [], EVIDENCE)
        _call(conn, caller, api_id, "hub-service")
    service_calls_repo.reconcile_service_call_targets(conn)

    findings = find_fan_imbalance(conn)

    fan_in = [f for f in findings if f["kind"] == "fan_in"]
    assert len(fan_in) == 1
    assert fan_in[0]["services"] == ["hub-service"]
    assert fan_in[0]["detail"]["count"] == 4


def test_find_fan_imbalance_does_not_flag_below_threshold(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    a = services_repo.ensure_service(conn, "a-service", "/tmp/a", "python")
    services_repo.ensure_service(conn, "b-service", "/tmp/b", "python")
    a_api = apis_repo.upsert_api(conn, a, "GET", "/a", "s", "d", [], EVIDENCE)
    _call(conn, a, a_api, "b-service")
    service_calls_repo.reconcile_service_call_targets(conn)

    assert find_fan_imbalance(conn) == []


def test_find_shared_database_flags_same_entity_on_same_engine(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    a = services_repo.ensure_service(conn, "a-service", "/tmp/a", "python")
    b = services_repo.ensure_service(conn, "b-service", "/tmp/b", "python")
    persistence_repo.replace_persistence_entities(
        conn, a, [{"name": "orders", "kind": "sql_table", "engine": "postgres", "schema_json": []}], EVIDENCE,
    )
    persistence_repo.replace_persistence_entities(
        conn, b, [{"name": "orders", "kind": "sql_table", "engine": "postgres", "schema_json": []}], EVIDENCE,
    )

    findings = find_shared_database(conn)

    assert len(findings) == 1
    assert set(findings[0]["services"]) == {"a-service", "b-service"}
    assert findings[0]["detail"]["entity"] == "orders"


def test_find_shared_database_ignores_unknown_engine(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    a = services_repo.ensure_service(conn, "a-service", "/tmp/a", "python")
    b = services_repo.ensure_service(conn, "b-service", "/tmp/b", "python")
    persistence_repo.replace_persistence_entities(
        conn, a, [{"name": "orders", "kind": "sql_table", "schema_json": []}], EVIDENCE,
    )
    persistence_repo.replace_persistence_entities(
        conn, b, [{"name": "orders", "kind": "sql_table", "schema_json": []}], EVIDENCE,
    )

    assert find_shared_database(conn) == []


def test_find_duplicate_external_integrations(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    a = services_repo.ensure_service(conn, "a-service", "/tmp/a", "python")
    b = services_repo.ensure_service(conn, "b-service", "/tmp/b", "python")
    a_api = apis_repo.upsert_api(conn, a, "GET", "/a", "s", "d", [], EVIDENCE)
    b_api = apis_repo.upsert_api(conn, b, "GET", "/b", "s", "d", [], EVIDENCE)
    _call(conn, a, a_api, "Stripe API", target_kind="external")
    _call(conn, b, b_api, "Stripe API", target_kind="external")

    findings = find_duplicate_external_integrations(conn)

    assert len(findings) == 1
    assert set(findings[0]["services"]) == {"a-service", "b-service"}
    assert findings[0]["detail"]["vendor"] == "Stripe API"


def test_recompute_architecture_view_persists_a_new_run_with_findings(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    a = services_repo.ensure_service(conn, "a-service", "/tmp/a", "python")
    b = services_repo.ensure_service(conn, "b-service", "/tmp/b", "python")
    a_api = apis_repo.upsert_api(conn, a, "GET", "/a", "s", "d", [], EVIDENCE)
    b_api = apis_repo.upsert_api(conn, b, "GET", "/b", "s", "d", [], EVIDENCE)
    _call(conn, a, a_api, "b-service")
    _call(conn, b, b_api, "a-service")
    service_calls_repo.reconcile_service_call_targets(conn)

    run_id = recompute_architecture_view(conn)

    findings = architecture_repo.list_findings(conn, run_id)
    assert any(f["kind"] == "cycle" for f in findings)


def test_recompute_architecture_view_creates_a_fresh_run_each_time(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    services_repo.ensure_service(conn, "a-service", "/tmp/a", "python")

    run_1 = recompute_architecture_view(conn)
    run_2 = recompute_architecture_view(conn)

    assert run_1 != run_2
    assert architecture_repo.latest_run_id(conn) == run_2
