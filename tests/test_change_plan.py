"""Contract tests for the bounded, evidence-first change planning entrypoint."""
import json

from jsonschema import validate

from orbitkb.db.connection import open_db
from orbitkb.db.repositories import change_plans
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

    plan = change_plans.get_plan(conn, int(result["plan_id"].removeprefix("cp_")))
    assert plan["change_surface_run_id"] is not None
    assert plan["status"] == "ready"
    assert plan["requested_tokens"] == 2200
    assert plan["estimated_tokens"] == result["budget"]["estimated_tokens"]
    assert plan["truncated"] == 0


def test_plan_change_rejects_an_invalid_token_budget_without_calling_the_backend(tmp_path):
    conn = _build_pix_fixture(tmp_path / "invalid-budget.db")
    backend = FakeBackend({})

    assert queries.plan_change(conn, backend, "Add a payment method", token_budget=0) == {
        "error": "token_budget must be between 1 and 2200 (got 0)",
    }
    assert backend.calls == 0


def test_plan_change_requires_a_contract_compatibility_decision_for_affected_event_consumers(tmp_path):
    conn = _build_pix_fixture(tmp_path / "event-decision.db")
    backend = FakeBackend({
        "primary": [{"service": "payments-service", "reason": "owns payment authorization", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    })

    result = queries.plan_change(conn, backend, "Add a payment method")

    assert result["status"] == "needs_decision"
    assert result["decision_points"] == [{
        "id": "event-compatibility:payments-service:payment_authorized",
        "question": "Will the payment_authorized event payload or compatibility change?",
        "why_blocking": "notification-service consumes this event; compatibility determines whether it must change.",
        "options": [
            "preserve backward compatibility",
            "version the event contract and update consumers",
        ],
        "recommended_default": "preserve backward compatibility unless a versioned rollout is approved",
        "owner": "payments-service",
        "contract": "payment_authorized",
        "consumers": ["notification-service"],
        "evidence": [],
    }]
    validate(result, load_schema("plan_change"))

    plan = change_plans.get_plan(conn, int(result["plan_id"].removeprefix("cp_")))
    assert json.loads(plan["decision_points_json"]) == result["decision_points"]


def test_refine_change_plan_persists_an_explicit_decision_without_retrieval(tmp_path):
    conn = _build_pix_fixture(tmp_path / "refine.db")
    backend = FakeBackend({
        "primary": [{"service": "payments-service", "reason": "owns payment authorization", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    })
    plan = queries.plan_change(conn, backend, "Add a payment method")

    result = queries.refine_change_plan(conn, plan["plan_id"], [{
        "id": "event-compatibility:payments-service:payment_authorized",
        "option": "preserve backward compatibility",
    }])

    assert backend.calls == 1
    assert result == {
        "plan_id": plan["plan_id"],
        "status": "ready",
        "selected_decisions": [{
            "id": "event-compatibility:payments-service:payment_authorized",
            "option": "preserve backward compatibility",
        }],
        "remaining_decision_points": [],
        "change_units": [{
            "id": "event-contract:payments-service:payment_authorized",
            "service": "payments-service",
            "target": {
                "role": "contract",
                "symbol": "message.publish:payment_authorized",
                "evidence": [],
            },
            "action": "validate",
            "reason": "preserve backward compatibility for payment_authorized before changing its producer.",
            "preconditions": [
                "event-compatibility:payments-service:payment_authorized=preserve backward compatibility",
            ],
            "related_contracts": ["payment_authorized"],
            "dependencies": ["notification-service"],
            "validation": [
                "verify payment_authorized remains compatible with notification-service",
            ],
            "confidence": 1.0,
            "evidence": [],
        }],
    }
    validate(result, load_schema("refine_change_plan"))
    persisted = change_plans.get_plan(conn, int(plan["plan_id"].removeprefix("cp_")))
    assert persisted["status"] == "ready"
    assert json.loads(persisted["selected_decisions_json"]) == result["selected_decisions"]
    assert json.loads(persisted["change_units_json"]) == result["change_units"]


def test_refine_change_plan_rejects_an_undeclared_option_without_mutating_the_plan(tmp_path):
    conn = _build_pix_fixture(tmp_path / "invalid-refine.db")
    plan = queries.plan_change(conn, FakeBackend({
        "primary": [{"service": "payments-service", "reason": "owns payment authorization", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    }), "Add a payment method")

    result = queries.refine_change_plan(conn, plan["plan_id"], [{
        "id": "event-compatibility:payments-service:payment_authorized",
        "option": "break all consumers",
    }])

    assert result == {"error": "unsupported option for decision: event-compatibility:payments-service:payment_authorized"}
    persisted = change_plans.get_plan(conn, int(plan["plan_id"].removeprefix("cp_")))
    assert persisted["status"] == "needs_decision"
    assert json.loads(persisted["selected_decisions_json"]) == []


def test_refine_change_plan_marks_the_contract_unit_for_modification_when_versioning_is_selected(tmp_path):
    conn = _build_pix_fixture(tmp_path / "versioned-event.db")
    plan = queries.plan_change(conn, FakeBackend({
        "primary": [{"service": "payments-service", "reason": "owns payment authorization", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    }), "Add a payment method")

    result = queries.refine_change_plan(conn, plan["plan_id"], [{
        "id": "event-compatibility:payments-service:payment_authorized",
        "option": "version the event contract and update consumers",
    }])

    unit = result["change_units"][0]
    assert unit["action"] == "modify"
    assert unit["reason"] == "version the event contract for payment_authorized before changing its producer."
    assert unit["dependencies"] == ["notification-service"]


def test_plan_change_persists_an_insufficient_evidence_plan_without_a_surface_run(tmp_path):
    conn = open_db(tmp_path / "empty.db")

    result = queries.plan_change(conn, FakeBackend({}), "unrelated xyz")

    plan = change_plans.get_plan(conn, int(result["plan_id"].removeprefix("cp_")))
    assert result["status"] == "insufficient_evidence"
    assert plan["change_surface_run_id"] is None
    assert plan["status"] == "insufficient_evidence"
