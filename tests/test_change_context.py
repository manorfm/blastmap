"""Compact epic context assembled from indexed facts and one change-surface result."""
from orbitkb.analysis.models import (
    AnalysisResult,
    EntryPoint,
    Evidence,
    StaticServiceCall,
)
from orbitkb.db.repositories import flows, repositories, services
from orbitkb.mcp import queries
from tests.test_change_surface import FakeBackend, _build_pix_fixture


def test_change_context_packages_bounded_service_facts_without_reading_source(tmp_path):
    conn = _build_pix_fixture(tmp_path / "context.db")
    backend = FakeBackend({
        "primary": [
            {"service": "checkout-service", "reason": "owns checkout", "confidence": 0.9},
            {"service": "payments-service", "reason": "owns payment authorization", "confidence": 0.8},
        ],
        "secondary": [{"service": "order-service", "reason": "observes payment", "confidence": 0.5}],
        "no_change": [],
    })

    result = queries.get_change_context(conn, backend, "Add a payment method", max_services=1)

    assert result["task"] == "Add a payment method"
    assert result["run_id"] is not None
    assert [item["service"] for item in result["impact"]["primary"]] == [
        "checkout-service", "payments-service",
    ]
    assert result["budget"] == {"max_services": 1, "returned_services": 1, "truncated": True}
    assert result["services"] == [{
        "service": "checkout-service",
        "repository": None,
        "stack": "python",
        "summary": "Owns the checkout entry point and forwards payment method.",
        "interfaces": [{"kind": "http", "method": "POST", "name": "/checkout"}],
        "outbound_dependencies": [{
            "service": "payments-service", "type": "HTTP", "target_kind": "internal",
            "reason": "authorize the pix payment for the order",
        }],
        "persistence": [],
        "messages": [],
    }]
    assert result["architecture_risks"] == []
    assert result["unknowns"]
    assert result["recommended_next_queries"]


def test_change_context_rejects_an_unbounded_service_budget(tmp_path):
    conn = _build_pix_fixture(tmp_path / "invalid-budget.db")

    assert queries.get_change_context(conn, FakeBackend({}), "Add a payment method", max_services=0) == {
        "error": "max_services must be between 1 and 5 (got 0)",
    }


def test_change_context_includes_resolved_static_remote_dependencies(tmp_path):
    conn = _build_pix_fixture(tmp_path / "static-dependency-context.db")
    checkout = services.get_service_by_name(conn, "checkout-service")
    payments = services.get_service_by_name(conn, "payments-service")
    evidence = Evidence("CheckoutService.java", 18, 18)
    flows.replace_analysis(
        conn,
        checkout["id"],
        AnalysisResult(static_service_calls=[
            StaticServiceCall(
                source="CheckoutService.submit", target_service="payments-service", protocol="http",
                target_method="POST", target_path="/authorizations", evidence=evidence,
            ),
        ]),
    )
    flows.replace_analysis(
        conn,
        payments["id"],
        AnalysisResult(entrypoints=[
            EntryPoint("http", "POST", "/authorizations", "PaymentsController.authorize", evidence),
        ]),
    )
    backend = FakeBackend({
        "primary": [{"service": "checkout-service", "reason": "owns checkout", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    })

    result = queries.get_change_context(conn, backend, "Add a payment method", max_services=1)

    assert result["services"][0]["static_outbound_dependencies"] == [{
        "source": "CheckoutService.submit", "service": "payments-service", "protocol": "http",
        "method": "POST", "path": "/authorizations",
        "resolved_target": {
            "service": "payments-service", "repository": None, "status": "endpoint_indexed",
            "entrypoint": {
                "method": "POST", "path": "/authorizations", "symbol": "PaymentsController.authorize",
                "evidence": {"file": "CheckoutService.java", "start_line": 18, "end_line": 18},
            },
        },
    }]


def test_change_context_omits_ambiguous_static_remote_dependencies(tmp_path):
    conn = _build_pix_fixture(tmp_path / "ambiguous-static-dependency-context.db")
    checkout = services.get_service_by_name(conn, "checkout-service")
    main_repository = repositories.ensure_repository(conn, "main", "/repos/main")
    conn.execute("UPDATE services SET repository_id = ? WHERE id = ?", (main_repository, checkout["id"]))
    conn.commit()
    evidence = Evidence("CheckoutService.java", 18, 18)
    flows.replace_analysis(
        conn,
        checkout["id"],
        AnalysisResult(static_service_calls=[
            StaticServiceCall(
                source="CheckoutService.submit", target_service="inventory", protocol="http",
                target_method="POST", target_path="/reservations", evidence=evidence,
            ),
        ]),
    )
    first_repository = repositories.ensure_repository(conn, "first", "/repos/first")
    second_repository = repositories.ensure_repository(conn, "second", "/repos/second")
    services.ensure_service(conn, "inventory", "/repos/first/inventory", "jvm-spring", first_repository)
    services.ensure_service(conn, "inventory", "/repos/second/inventory", "jvm-spring", second_repository)
    backend = FakeBackend({
        "primary": [{"service": "checkout-service", "reason": "owns checkout", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    })

    result = queries.get_change_context(
        conn, backend, "Add a payment method", max_services=1, repository="main",
    )

    assert "static_outbound_dependencies" not in result["services"][0]
