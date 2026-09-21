"""TDD coverage for generation/architecture.py — deterministic, whole-graph structural
findings computed purely from already-seeded db.repositories.* facts, no LLM."""
from pathlib import Path

from orbitkb.analysis.models import (
    AnalysisResult,
    EntryPoint,
    Evidence,
    FlowBoundary,
    FlowEdge,
)
from orbitkb.db.connection import open_db
from orbitkb.db.repositories import apis as apis_repo
from orbitkb.db.repositories import architecture as architecture_repo
from orbitkb.db.repositories import flows as flows_repo
from orbitkb.db.repositories import persistence as persistence_repo
from orbitkb.db.repositories import repositories as repositories_repo
from orbitkb.db.repositories import service_calls as service_calls_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.generation.architecture import (
    diff_architecture_runs,
    find_cycles,
    find_duplicate_external_integrations,
    find_fan_imbalance,
    find_flow_hypotheses,
    find_shared_database,
    recompute_architecture_view,
)
from orbitkb.mcp import queries

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


def test_flow_hypotheses_report_graphql_policy_leakage_and_non_atomic_publish_with_evidence(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = services_repo.ensure_service(conn, "checkout-bff", "/tmp/checkout", "node-ts")
    evidence = Evidence("resolvers.ts", 10, 10)
    flows_repo.replace_analysis(
        conn,
        service_id,
        AnalysisResult(
            entrypoints=[EntryPoint("graphql", "MUTATION", "checkout", "Mutation.checkout", evidence)],
            edges=[
                FlowEdge("Mutation.checkout", "ordersRepository.save", "writes", evidence),
                FlowEdge("Mutation.checkout", "events.publish", "publishes", Evidence("resolvers.ts", 11, 11)),
            ],
        ),
    )

    findings = find_flow_hypotheses(conn)

    bff = next(finding for finding in findings if finding["kind"] == "possible_bff_domain_leakage")
    non_atomic = next(finding for finding in findings if finding["kind"] == "possible_non_atomic_publish")
    assert bff["services"] == ["checkout-bff"]
    assert bff["detail"]["confidence"] == 0.6
    assert bff["detail"]["evidence"] == [
        {"file": "resolvers.ts", "start_line": 10, "end_line": 10},
        {"file": "resolvers.ts", "start_line": 11, "end_line": 11},
    ]
    assert bff["detail"]["unknowns"]
    assert non_atomic["detail"]["confidence"] == 0.5
    assert len(non_atomic["detail"]["evidence"]) == 2

    recompute_architecture_view(conn)
    response = queries.find_architecture_smells(conn)
    exposed = next(item for item in response["findings"] if item["kind"] == "possible_bff_domain_leakage")
    assert exposed["confidence"] == 0.6
    assert exposed["evidence"] == bff["detail"]["evidence"]
    assert exposed["unknowns"] == bff["detail"]["unknowns"]


def test_architecture_findings_qualify_duplicate_service_names_by_repository(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    sales_repo = repositories_repo.ensure_repository(conn, "sales", "/tmp/sales")
    support_repo = repositories_repo.ensure_repository(conn, "support", "/tmp/support")
    sales_orders = services_repo.ensure_service(conn, "orders", "/tmp/sales/orders", "node-ts", sales_repo)
    support_orders = services_repo.ensure_service(conn, "orders", "/tmp/support/orders", "node-ts", support_repo)
    evidence = Evidence("resolver.ts", 8, 8)
    for service_id, symbol in ((sales_orders, "Mutation.createOrder"), (support_orders, "Mutation.createTicket")):
        flows_repo.replace_analysis(
            conn,
            service_id,
            AnalysisResult(
                entrypoints=[EntryPoint("graphql", "MUTATION", symbol, symbol, evidence)],
                edges=[FlowEdge(symbol, "repository.save", "writes", evidence)],
            ),
        )

    findings = find_flow_hypotheses(conn)

    assert {finding["services"][0] for finding in findings} == {"sales/orders", "support/orders"}


def test_architecture_trend_keeps_flow_hypotheses_for_each_entrypoint(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = services_repo.ensure_service(conn, "checkout", "/tmp/checkout", "node-ts")
    evidence = Evidence("resolver.ts", 8, 8)
    first = EntryPoint("graphql", "MUTATION", "checkout", "Mutation.checkout", evidence)
    flows_repo.replace_analysis(
        conn, service_id,
        AnalysisResult(entrypoints=[first], edges=[FlowEdge(first.symbol, "repository.save", "writes", evidence)]),
    )
    run_1 = recompute_architecture_view(conn)
    second = EntryPoint("graphql", "MUTATION", "cancelCheckout", "Mutation.cancelCheckout", Evidence("resolver.ts", 20, 20))
    flows_repo.replace_analysis(
        conn, service_id,
        AnalysisResult(
            entrypoints=[first, second],
            edges=[
                FlowEdge(first.symbol, "repository.save", "writes", evidence),
                FlowEdge(second.symbol, "repository.cancel", "writes", second.evidence),
            ],
        ),
    )

    diff = diff_architecture_runs(conn, run_1, recompute_architecture_view(conn))

    assert [(finding["kind"], finding["detail"]["entrypoint"]["symbol"]) for finding in diff["new_findings"]] == [
        ("possible_bff_domain_leakage", "Mutation.cancelCheckout")
    ]


def test_non_atomic_publish_hypothesis_is_suppressed_when_a_transaction_boundary_exists(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = services_repo.ensure_service(conn, "orders", "/tmp/orders", "jvm-spring")
    evidence = Evidence("OrdersController.java", 10, 10)
    flows_repo.replace_analysis(
        conn,
        service_id,
        AnalysisResult(
            entrypoints=[EntryPoint("http", "POST", "/orders", "Orders.create", evidence)],
            edges=[
                FlowEdge("Orders.create", "repository.save", "writes", evidence),
                FlowEdge("Orders.create", "publisher.publish", "publishes", Evidence("OrdersController.java", 11, 11)),
            ],
            boundaries=[FlowBoundary("Orders.create", "transaction", evidence)],
        ),
    )

    assert find_flow_hypotheses(conn) == []


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


def test_diff_architecture_runs_reports_a_new_finding(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    a = services_repo.ensure_service(conn, "a-service", "/tmp/a", "python")
    services_repo.ensure_service(conn, "b-service", "/tmp/b", "python")
    a_api = apis_repo.upsert_api(conn, a, "GET", "/a", "s", "d", [], EVIDENCE)
    run_1 = recompute_architecture_view(conn)  # no edges yet: no findings

    _call(conn, a, a_api, "b-service")
    service_calls_repo.reconcile_service_call_targets(conn)
    b = services_repo.get_service_by_name(conn, "b-service")["id"]
    b_api = apis_repo.upsert_api(conn, b, "GET", "/b", "s", "d", [], EVIDENCE)
    _call(conn, b, b_api, "a-service")
    service_calls_repo.reconcile_service_call_targets(conn)
    run_2 = recompute_architecture_view(conn)  # cycle now exists

    diff = diff_architecture_runs(conn, run_1, run_2)

    assert len(diff["new_findings"]) == 1
    assert diff["new_findings"][0]["kind"] == "cycle"
    assert diff["resolved_findings"] == []
    assert diff["count_deltas"] == []


def test_diff_architecture_runs_reports_a_resolved_finding(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    a = services_repo.ensure_service(conn, "a-service", "/tmp/a", "python")
    b = services_repo.ensure_service(conn, "b-service", "/tmp/b", "python")
    a_api = apis_repo.upsert_api(conn, a, "GET", "/a", "s", "d", [], EVIDENCE)
    b_api = apis_repo.upsert_api(conn, b, "GET", "/b", "s", "d", [], EVIDENCE)
    _call(conn, a, a_api, "b-service")
    _call(conn, b, b_api, "a-service")
    service_calls_repo.reconcile_service_call_targets(conn)
    run_1 = recompute_architecture_view(conn)  # cycle exists

    service_calls_repo.replace_calls_for_api(conn, b, b_api, [], EVIDENCE)  # break the cycle
    service_calls_repo.reconcile_service_call_targets(conn)
    run_2 = recompute_architecture_view(conn)

    diff = diff_architecture_runs(conn, run_1, run_2)

    assert diff["new_findings"] == []
    assert len(diff["resolved_findings"]) == 1
    assert diff["resolved_findings"][0]["kind"] == "cycle"


def test_diff_architecture_runs_reports_a_fan_in_count_delta(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    services_repo.ensure_service(conn, "hub-service", "/tmp/hub", "python")
    for i in range(4):
        caller = services_repo.ensure_service(conn, f"caller-{i}-service", f"/tmp/c{i}", "python")
        api_id = apis_repo.upsert_api(conn, caller, "GET", "/x", "s", "d", [], EVIDENCE)
        _call(conn, caller, api_id, "hub-service")
    service_calls_repo.reconcile_service_call_targets(conn)
    run_1 = recompute_architecture_view(conn)  # fan_in count = 4

    caller_5 = services_repo.ensure_service(conn, "caller-5-service", "/tmp/c5", "python")
    api_5 = apis_repo.upsert_api(conn, caller_5, "GET", "/x", "s", "d", [], EVIDENCE)
    _call(conn, caller_5, api_5, "hub-service")
    service_calls_repo.reconcile_service_call_targets(conn)
    run_2 = recompute_architecture_view(conn)  # fan_in count = 5

    diff = diff_architecture_runs(conn, run_1, run_2)

    assert diff["new_findings"] == []
    assert diff["resolved_findings"] == []
    fan_in_delta = next(d for d in diff["count_deltas"] if d["kind"] == "fan_in")
    assert fan_in_delta["previous_count"] == 4
    assert fan_in_delta["current_count"] == 5
    assert fan_in_delta["services"] == ["hub-service"]


def test_diff_architecture_runs_is_empty_when_nothing_changed(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    a = services_repo.ensure_service(conn, "a-service", "/tmp/a", "python")
    b = services_repo.ensure_service(conn, "b-service", "/tmp/b", "python")
    a_api = apis_repo.upsert_api(conn, a, "GET", "/a", "s", "d", [], EVIDENCE)
    b_api = apis_repo.upsert_api(conn, b, "GET", "/b", "s", "d", [], EVIDENCE)
    _call(conn, a, a_api, "b-service")
    _call(conn, b, b_api, "a-service")
    service_calls_repo.reconcile_service_call_targets(conn)
    run_1 = recompute_architecture_view(conn)
    run_2 = recompute_architecture_view(conn)  # nothing changed on disk

    diff = diff_architecture_runs(conn, run_1, run_2)

    assert diff == {"new_findings": [], "resolved_findings": [], "count_deltas": []}
