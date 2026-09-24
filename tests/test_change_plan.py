"""Contract tests for the bounded, evidence-first change planning entrypoint."""
from jsonschema import validate

from orbitkb.generation.llm_harness import load_schema
from orbitkb.mcp import queries
from tests.test_change_surface import FakeBackend, _build_pix_fixture


def test_plan_change_wraps_the_indexed_surface_in_a_stable_initial_contract(tmp_path):
    conn = _build_pix_fixture(tmp_path / "plan.db")
    backend = FakeBackend({
        "primary": [{"service": "checkout-service", "reason": "owns checkout", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    })

    result = queries.plan_change(conn, backend, "Add a payment method", token_budget=2200)

    assert result["plan_id"].startswith("cp_")
    assert result["status"] == "ready"
    assert result["surface"]["primary"] == [{
        "service": "checkout-service", "reason": "owns checkout", "confidence": 0.9,
        "evidence": [{"file": "checkout.py", "start_line": 1, "end_line": 20}],
    }]
    assert result["decision_points"] == []
    assert result["change_units"] == []
    assert result["budget"]["requested_tokens"] == 2200
    assert result["budget"]["estimated_tokens"] > 0
    assert result["budget"]["truncated"] is False
    validate(result, load_schema("plan_change"))


def test_plan_change_rejects_an_invalid_token_budget_without_calling_the_backend(tmp_path):
    conn = _build_pix_fixture(tmp_path / "invalid-budget.db")
    backend = FakeBackend({})

    assert queries.plan_change(conn, backend, "Add a payment method", token_budget=0) == {
        "error": "token_budget must be between 1 and 2200 (got 0)",
    }
    assert backend.calls == 0
