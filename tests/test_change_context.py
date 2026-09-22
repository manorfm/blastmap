"""Compact epic context assembled from indexed facts and one change-surface result."""
from tests.test_change_surface import FakeBackend, _build_pix_fixture

from orbitkb.mcp import queries


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
