"""Fact-mutation regression checks for deterministic architecture-smell rules.

Each test creates the minimum evidence that should trigger a finding, then changes
one fact that invalidates that evidence. This guards rule boundaries without
pretending that a source-only heuristic proves a runtime architecture verdict.
"""
from pathlib import Path

from orbitkb.analysis.models import (
    AnalysisResult,
    EntryPoint,
    ErrorContract,
    Evidence,
    FlowEdge,
    StaticServiceCall,
)
from orbitkb.db.connection import open_db
from orbitkb.db.repositories import apis as apis_repo
from orbitkb.db.repositories import flows as flows_repo
from orbitkb.db.repositories import persistence as persistence_repo
from orbitkb.db.repositories import service_calls as service_calls_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.generation.architecture import (
    find_cycles,
    find_error_semantics_lost,
    find_fan_imbalance,
    find_message_consumers_without_recovery_policy,
    find_overbroad_exception_handlers,
    find_read_entrypoint_side_effects,
    find_shared_database,
    find_unmapped_downstream_errors,
)

EVIDENCE = [{"file": "main.py", "start_line": 1, "end_line": 5}]
STATIC_EVIDENCE = Evidence("handler.ts", 4, 6)


def test_cycle_finding_disappears_when_one_return_edge_is_removed(tmp_path: Path):
    conn = open_db(tmp_path / "cycle.db")
    left = services_repo.ensure_service(conn, "left", "/tmp/left", "go")
    right = services_repo.ensure_service(conn, "right", "/tmp/right", "go")
    left_api = apis_repo.upsert_api(conn, left, "POST", "/left", "s", "d", [], EVIDENCE)
    right_api = apis_repo.upsert_api(conn, right, "POST", "/right", "s", "d", [], EVIDENCE)
    _replace_calls(conn, left, left_api, ["right"])
    _replace_calls(conn, right, right_api, ["left"])

    assert _kinds(find_cycles(conn)) == {"cycle"}

    _replace_calls(conn, right, right_api, [])
    assert find_cycles(conn) == []


def test_fan_out_finding_disappears_below_the_configured_boundary(tmp_path: Path):
    conn = open_db(tmp_path / "fan-out.db")
    source = services_repo.ensure_service(conn, "gateway", "/tmp/gateway", "go")
    for index in range(4):
        services_repo.ensure_service(conn, f"target-{index}", f"/tmp/target-{index}", "go")
    api = apis_repo.upsert_api(conn, source, "POST", "/work", "s", "d", [], EVIDENCE)
    _replace_calls(conn, source, api, [f"target-{index}" for index in range(4)])

    assert _kinds(find_fan_imbalance(conn)) == {"fan_out"}

    _replace_calls(conn, source, api, [f"target-{index}" for index in range(3)])
    assert find_fan_imbalance(conn) == []


def test_shared_database_finding_disappears_when_storage_engines_differ(tmp_path: Path):
    conn = open_db(tmp_path / "storage.db")
    orders = services_repo.ensure_service(conn, "orders", "/tmp/orders", "go")
    reporting = services_repo.ensure_service(conn, "reporting", "/tmp/reporting", "node-ts")
    persistence_repo.replace_persistence_entities(
        conn, orders, [{"name": "orders", "kind": "sql_table", "engine": "postgres", "schema_json": []}], EVIDENCE,
    )
    persistence_repo.replace_persistence_entities(
        conn, reporting, [{"name": "orders", "kind": "sql_table", "engine": "postgres", "schema_json": []}], EVIDENCE,
    )

    assert _kinds(find_shared_database(conn)) == {"shared_database"}

    persistence_repo.replace_persistence_entities(
        conn, reporting, [{"name": "orders", "kind": "document", "engine": "mongodb", "schema_json": []}], EVIDENCE,
    )
    assert find_shared_database(conn) == []


def test_read_side_effect_finding_disappears_when_transport_becomes_a_command(tmp_path: Path):
    conn = open_db(tmp_path / "read-side-effect.db")
    service = services_repo.ensure_service(conn, "catalog", "/tmp/catalog", "node-ts")
    entrypoint = EntryPoint("http", "GET", "/catalog/refresh", "Catalog.refresh", STATIC_EVIDENCE)
    _replace_static_flow(conn, service, entrypoint)

    assert _kinds(find_read_entrypoint_side_effects(conn)) == {"possible_read_entrypoint_side_effect"}

    _replace_static_flow(conn, service, EntryPoint("http", "POST", "/catalog/refresh", "Catalog.refresh", STATIC_EVIDENCE))
    assert find_read_entrypoint_side_effects(conn) == []


def test_message_recovery_finding_disappears_when_a_dead_letter_route_is_proven(tmp_path: Path):
    conn = open_db(tmp_path / "consumer.db")
    service = services_repo.ensure_service(conn, "billing", "/tmp/billing", "node-ts")
    consumer = EntryPoint("message", "CONSUME", "billing.created", "message.consume:billing.created", STATIC_EVIDENCE)
    _replace_consumer_contract(conn, service, consumer, {})

    assert _kinds(find_message_consumers_without_recovery_policy(conn)) == {
        "possible_message_consumer_without_recovery_policy",
    }

    _replace_consumer_contract(conn, service, consumer, {"dead_letter_routing_key": "billing.dlq"})
    assert find_message_consumers_without_recovery_policy(conn) == []


def test_overbroad_exception_handler_disappears_when_the_mapping_becomes_specific(tmp_path: Path):
    conn = open_db(tmp_path / "error-handler.db")
    service = services_repo.ensure_service(conn, "orders", "/tmp/orders", "jvm-spring")
    broad = ErrorContract(
        source="ApiExceptionHandler.handle", role="maps", error_kind="unexpected",
        internal_type="Exception", protocol="http", transport_code="500", public_code=None,
        exposes_internal_detail=False, retryability="unknown", evidence=STATIC_EVIDENCE,
    )
    flows_repo.replace_analysis(conn, service, AnalysisResult(error_contracts=[broad]))

    assert _kinds(find_overbroad_exception_handlers(conn)) == {"possible_overbroad_exception_handler"}

    specific = ErrorContract(
        source="ApiExceptionHandler.handle", role="maps", error_kind="conflict",
        internal_type="InsufficientStockException", protocol="http", transport_code="409", public_code=None,
        exposes_internal_detail=False, retryability="not_retryable", evidence=STATIC_EVIDENCE,
    )
    flows_repo.replace_analysis(conn, service, AnalysisResult(error_contracts=[specific]))

    assert find_overbroad_exception_handlers(conn) == []


def test_error_semantics_lost_disappears_when_a_conflict_is_mapped_to_409(tmp_path: Path):
    conn = open_db(tmp_path / "error-semantics.db")
    service = services_repo.ensure_service(conn, "orders", "/tmp/orders", "jvm-spring")
    raised = ErrorContract(
        source="StockReservation.reserve", role="raises", error_kind="conflict",
        internal_type="InsufficientStockException", protocol="internal", transport_code=None,
        public_code=None, exposes_internal_detail=False, retryability="not_retryable", evidence=STATIC_EVIDENCE,
    )
    degraded = ErrorContract(
        source="ApiExceptionHandler.handleStock", role="maps", error_kind="unexpected",
        internal_type="InsufficientStockException", protocol="http", transport_code="500",
        public_code=None, exposes_internal_detail=False, retryability="unknown", evidence=STATIC_EVIDENCE,
    )
    flows_repo.replace_analysis(conn, service, AnalysisResult(error_contracts=[raised, degraded]))

    assert _kinds(find_error_semantics_lost(conn)) == {"possible_error_semantics_lost"}

    preserved = ErrorContract(
        source="ApiExceptionHandler.handleStock", role="maps", error_kind="conflict",
        internal_type="InsufficientStockException", protocol="http", transport_code="409",
        public_code=None, exposes_internal_detail=False, retryability="not_retryable", evidence=STATIC_EVIDENCE,
    )
    flows_repo.replace_analysis(conn, service, AnalysisResult(error_contracts=[raised, preserved]))

    assert find_error_semantics_lost(conn) == []


def test_unmapped_downstream_error_disappears_when_caller_maps_the_known_error(tmp_path: Path):
    conn = open_db(tmp_path / "downstream-error.db")
    checkout = services_repo.ensure_service(conn, "checkout", "/tmp/checkout", "jvm-spring")
    inventory = services_repo.ensure_service(conn, "inventory", "/tmp/inventory", "jvm-spring")
    checkout_api = apis_repo.upsert_api(conn, checkout, "POST", "/orders", "", "", [], EVIDENCE)
    service_calls_repo.replace_calls_for_api(
        conn,
        checkout,
        checkout_api,
        [{
            "to_service_name": "inventory", "call_kind": "http", "reason": "reserve stock",
            "data_needed": [], "purpose_kind": "validation", "confidence": 1.0,
            "target_kind": "internal",
        }],
        EVIDENCE,
    )
    downstream = ErrorContract(
        source="InventoryExceptionHandler.stock", role="maps", error_kind="conflict",
        internal_type="InsufficientStockException", protocol="http", transport_code="409",
        public_code="OUT_OF_STOCK", exposes_internal_detail=False, retryability="not_retryable",
        evidence=STATIC_EVIDENCE,
    )
    flows_repo.replace_analysis(conn, inventory, AnalysisResult(error_contracts=[downstream]))

    assert _kinds(find_unmapped_downstream_errors(conn)) == {"possible_unmapped_downstream_error"}

    caller_mapping = ErrorContract(
        source="CheckoutExceptionHandler.stock", role="maps", error_kind="conflict",
        internal_type="InsufficientStockException", protocol="http", transport_code="409",
        public_code="OUT_OF_STOCK", exposes_internal_detail=False, retryability="not_retryable",
        evidence=STATIC_EVIDENCE,
    )
    flows_repo.replace_analysis(conn, checkout, AnalysisResult(error_contracts=[caller_mapping]))

    assert find_unmapped_downstream_errors(conn) == []


def test_unmapped_downstream_error_uses_a_proven_feign_client_call(tmp_path: Path):
    conn = open_db(tmp_path / "static-downstream-error.db")
    checkout = services_repo.ensure_service(conn, "checkout", "/tmp/checkout", "jvm-spring")
    inventory = services_repo.ensure_service(conn, "inventory", "/tmp/inventory", "jvm-spring")
    feign_call = StaticServiceCall(
        source="CheckoutService.checkout", target_service="inventory", protocol="http",
        target_method="POST", target_path="/reservations", evidence=STATIC_EVIDENCE,
    )
    downstream = ErrorContract(
        source="InventoryExceptionHandler.stock", role="maps", error_kind="conflict",
        internal_type="InsufficientStockException", protocol="http", transport_code="409",
        public_code="OUT_OF_STOCK", exposes_internal_detail=False, retryability="not_retryable",
        evidence=STATIC_EVIDENCE,
    )
    flows_repo.replace_analysis(conn, checkout, AnalysisResult(static_service_calls=[feign_call]))
    flows_repo.replace_analysis(conn, inventory, AnalysisResult(error_contracts=[downstream]))

    findings = find_unmapped_downstream_errors(conn)

    assert _kinds(findings) == {"possible_unmapped_downstream_error"}
    assert findings[0]["detail"]["caller"] == {
        "service": "checkout", "symbol": "CheckoutService.checkout",
        "method": "POST", "path": "/reservations",
    }
    assert findings[0]["detail"]["confidence"] == 0.6


def test_unmapped_downstream_error_scopes_static_call_to_the_target_endpoint_flow(tmp_path: Path):
    conn = open_db(tmp_path / "endpoint-scoped-downstream-error.db")
    checkout = services_repo.ensure_service(conn, "checkout", "/tmp/checkout", "jvm-spring")
    inventory = services_repo.ensure_service(conn, "inventory", "/tmp/inventory", "jvm-spring")
    feign_call = StaticServiceCall(
        source="CheckoutService.checkout", target_service="inventory", protocol="http",
        target_method="POST", target_path="/reservations", evidence=STATIC_EVIDENCE,
    )
    reservation_error = ErrorContract(
        source="InventoryService.reserve", role="raises", error_kind="conflict",
        internal_type="InsufficientStockException", protocol="http", transport_code="409",
        public_code="OUT_OF_STOCK", exposes_internal_detail=False, retryability="not_retryable",
        evidence=STATIC_EVIDENCE,
    )
    unrelated_error = ErrorContract(
        source="InventoryService.reconcile", role="raises", error_kind="not_found",
        internal_type="SettlementNotFoundException", protocol="http", transport_code="404",
        public_code=None, exposes_internal_detail=False, retryability="not_retryable",
        evidence=STATIC_EVIDENCE,
    )
    flows_repo.replace_analysis(conn, checkout, AnalysisResult(static_service_calls=[feign_call]))
    flows_repo.replace_analysis(
        conn,
        inventory,
        AnalysisResult(
            entrypoints=[EntryPoint("http", "POST", "/reservations", "InventoryController.reserve", STATIC_EVIDENCE)],
            edges=[FlowEdge("InventoryController.reserve", "InventoryService.reserve", "invokes", STATIC_EVIDENCE)],
            error_contracts=[reservation_error, unrelated_error],
        ),
    )

    findings = find_unmapped_downstream_errors(conn)

    assert len(findings) == 1
    assert findings[0]["detail"]["downstream"]["error_type"] == "InsufficientStockException"
    assert findings[0]["detail"]["scope"] == "endpoint_flow"
    assert findings[0]["detail"]["confidence"] == 0.75


def _replace_calls(conn, service_id: int, api_id: int, targets: list[str]) -> None:
    service_calls_repo.replace_calls_for_api(
        conn,
        service_id,
        api_id,
        [
            {
                "to_service_name": target,
                "call_kind": "http",
                "reason": "test relation",
                "data_needed": [],
                "purpose_kind": "other",
                "confidence": 0.9,
                "target_kind": "internal",
            }
            for target in targets
        ],
        EVIDENCE,
    )
    service_calls_repo.reconcile_service_call_targets(conn)


def _replace_static_flow(conn, service_id: int, entrypoint: EntryPoint) -> None:
    flows_repo.replace_analysis(
        conn,
        service_id,
        AnalysisResult(entrypoints=[entrypoint], edges=[FlowEdge(entrypoint.symbol, "repository.save", "writes", STATIC_EVIDENCE)]),
    )


def _replace_consumer_contract(conn, service_id: int, consumer: EntryPoint, recovery: dict) -> None:
    flows_repo.replace_analysis(
        conn,
        service_id,
        AnalysisResult(
            entrypoints=[consumer],
            contracts={
                consumer.symbol: {
                    "transport": "rabbitmq",
                    "direction": "consumes",
                    "queue": consumer.name,
                    **recovery,
                },
            },
        ),
    )


def _kinds(findings: list[dict]) -> set[str]:
    return {finding["kind"] for finding in findings}
