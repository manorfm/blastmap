from orbitkb.analysis.models import (
    AnalysisResult,
    ErrorContract,
    Evidence,
    ResiliencePolicy,
    StaticServiceCall,
)
from orbitkb.db.connection import open_db
from orbitkb.db.repositories import flows, services


def test_static_error_contracts_are_replaced_with_the_flow_snapshot(tmp_path):
    conn = open_db(tmp_path / "errors.db")
    service_id = services.ensure_service(conn, "orders", "/repos/orders", "jvm-spring")
    evidence = Evidence("OrdersController.java", 18, 21)

    flows.replace_analysis(
        conn,
        service_id,
        AnalysisResult(error_contracts=[
            ErrorContract(
                source="OrdersController.create",
                role="maps",
                error_kind="conflict",
                internal_type="InsufficientStock",
                protocol="http",
                transport_code="409",
                public_code="OUT_OF_STOCK",
                exposes_internal_detail=False,
                retryability="not_retryable",
                evidence=evidence,
            ),
        ]),
    )

    assert [dict(row) for row in flows.list_static_error_contracts(conn, service_id)] == [{
        "source": "OrdersController.create",
        "role": "maps",
        "error_kind": "conflict",
        "internal_type": "InsufficientStock",
        "protocol": "http",
        "transport_code": "409",
        "public_code": "OUT_OF_STOCK",
        "exposes_internal_detail": 0,
        "retryability": "not_retryable",
        "file_path": "OrdersController.java",
        "start_line": 18,
        "end_line": 21,
    }]

    flows.replace_analysis(conn, service_id, AnalysisResult())

    assert flows.list_static_error_contracts(conn, service_id) == []


def test_static_service_calls_are_replaced_with_the_flow_snapshot(tmp_path):
    conn = open_db(tmp_path / "service-calls.db")
    service_id = services.ensure_service(conn, "checkout", "/repos/checkout", "jvm-spring")
    evidence = Evidence("CheckoutService.java", 24, 24)

    flows.replace_analysis(
        conn,
        service_id,
        AnalysisResult(static_service_calls=[
            StaticServiceCall(
                source="CheckoutService.checkout",
                target_service="inventory",
                protocol="http",
                target_method="POST",
                target_path="/reservations",
                evidence=evidence,
            ),
        ]),
    )

    assert [dict(row) for row in flows.list_static_service_calls(conn, service_id)] == [{
        "source": "CheckoutService.checkout",
        "target_service": "inventory",
        "protocol": "http",
        "target_method": "POST",
        "target_path": "/reservations",
        "file_path": "CheckoutService.java",
        "start_line": 24,
        "end_line": 24,
    }]

    flows.replace_analysis(conn, service_id, AnalysisResult())

    assert flows.list_static_service_calls(conn, service_id) == []


def test_static_resilience_policies_are_replaced_with_the_flow_snapshot(tmp_path):
    conn = open_db(tmp_path / "resilience.db")
    service_id = services.ensure_service(conn, "checkout", "/repos/checkout", "jvm-spring")
    evidence = Evidence("CheckoutClient.java", 24, 24)

    flows.replace_analysis(
        conn,
        service_id,
        AnalysisResult(resilience_policies=[
            ResiliencePolicy(
                source="CheckoutClient.reserve",
                kind="timeout",
                mechanism="reactor",
                value=2_000,
                unit="milliseconds",
                evidence=evidence,
            ),
        ]),
    )

    assert [dict(row) for row in flows.list_static_resilience_policies(conn, service_id)] == [{
        "source": "CheckoutClient.reserve",
        "kind": "timeout",
        "mechanism": "reactor",
        "value": 2_000,
        "unit": "milliseconds",
        "file_path": "CheckoutClient.java",
        "start_line": 24,
        "end_line": 24,
    }]

    flows.replace_analysis(conn, service_id, AnalysisResult())

    assert flows.list_static_resilience_policies(conn, service_id) == []


def test_static_error_contracts_for_sources_filters_by_source_symbol(tmp_path):
    conn = open_db(tmp_path / "errors-for-sources.db")
    service_id = services.ensure_service(conn, "orders", "/repos/orders", "jvm-spring")
    evidence = Evidence("OrdersController.java", 18, 21)

    flows.replace_analysis(
        conn,
        service_id,
        AnalysisResult(error_contracts=[
            ErrorContract(
                source="OrdersController.create", role="maps", error_kind="conflict",
                internal_type="InsufficientStock", protocol="http", transport_code="409",
                public_code="OUT_OF_STOCK", exposes_internal_detail=False, retryability="not_retryable",
                evidence=evidence,
            ),
            ErrorContract(
                source="OrdersController.cancel", role="maps", error_kind="not_found",
                internal_type="OrderNotFound", protocol="http", transport_code="404",
                public_code="ORDER_NOT_FOUND", exposes_internal_detail=False, retryability="not_retryable",
                evidence=evidence,
            ),
        ]),
    )

    assert [row["source"] for row in flows.list_static_error_contracts_for_sources(
        conn, service_id, {"OrdersController.create"},
    )] == ["OrdersController.create"]
    assert flows.list_static_error_contracts_for_sources(conn, service_id, set()) == []


def test_static_service_calls_for_sources_filters_by_source_symbol(tmp_path):
    conn = open_db(tmp_path / "service-calls-for-sources.db")
    service_id = services.ensure_service(conn, "checkout", "/repos/checkout", "jvm-spring")
    evidence = Evidence("CheckoutService.java", 24, 24)

    flows.replace_analysis(
        conn,
        service_id,
        AnalysisResult(static_service_calls=[
            StaticServiceCall(
                source="CheckoutService.checkout", target_service="inventory", protocol="http",
                target_method="POST", target_path="/reservations", evidence=evidence,
            ),
            StaticServiceCall(
                source="CheckoutService.refund", target_service="payments", protocol="http",
                target_method="POST", target_path="/refunds", evidence=evidence,
            ),
        ]),
    )

    assert [row["source"] for row in flows.list_static_service_calls_for_sources(
        conn, service_id, {"CheckoutService.checkout"},
    )] == ["CheckoutService.checkout"]
    assert flows.list_static_service_calls_for_sources(conn, service_id, set()) == []


def test_static_resilience_policies_for_sources_filters_by_source_symbol(tmp_path):
    conn = open_db(tmp_path / "resilience-for-sources.db")
    service_id = services.ensure_service(conn, "checkout", "/repos/checkout", "jvm-spring")
    evidence = Evidence("CheckoutClient.java", 24, 24)

    flows.replace_analysis(
        conn,
        service_id,
        AnalysisResult(resilience_policies=[
            ResiliencePolicy(
                source="CheckoutClient.reserve", kind="timeout", mechanism="reactor",
                value=2_000, unit="milliseconds", evidence=evidence,
            ),
            ResiliencePolicy(
                source="CheckoutClient.refund", kind="retry", mechanism="reactor",
                value=3, unit="attempts", evidence=evidence,
            ),
        ]),
    )

    assert [row["source"] for row in flows.list_static_resilience_policies_for_sources(
        conn, service_id, {"CheckoutClient.reserve"},
    )] == ["CheckoutClient.reserve"]
    assert flows.list_static_resilience_policies_for_sources(conn, service_id, set()) == []
