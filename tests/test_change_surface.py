"""Unit tests for generation.change_surface, the task-to-service impact analysis
that backs the find_change_surface MCP tool. The LLM backend is faked so these run
deterministically without a real subprocess call — matching the harness's own
"no tool/file access, evidence given in the prompt" contract.
"""
from pathlib import Path

from blastmap.db import repository
from blastmap.db.connection import open_db
from blastmap.generation import change_surface


class FakeBackend:
    name = "fake"

    def __init__(self, response: dict):
        self.response = response
        self.calls = 0

    def generate(self, prompt: str, schema: dict, cwd: Path) -> dict:
        self.calls += 1
        return self.response


def _build_pix_fixture(db_path: Path):
    conn = open_db(db_path)
    checkout_id = repository.ensure_service(conn, "checkout-service", "/tmp/checkout", "python")
    payments_id = repository.ensure_service(conn, "payments-service", "/tmp/payments", "node-ts")
    order_id = repository.ensure_service(conn, "order-service", "/tmp/order", "python")
    notif_id = repository.ensure_service(conn, "notification-service", "/tmp/notif", "python")

    repository.update_service_overview(conn, checkout_id, "Owns the checkout entry point and forwards payment method.", "L")
    repository.update_service_overview(conn, payments_id, "Owns payment method resolution and payment authorization.", "L")
    repository.update_service_overview(conn, order_id, "Consumes payment confirmation to create orders.", "L")
    repository.update_service_overview(conn, notif_id, "Sends emails when an order ships.", "L")

    api_id = repository.upsert_api(
        conn, checkout_id, "POST", "/checkout", "starts checkout", "desc", [],
        [{"file": "checkout.py", "start_line": 1, "end_line": 20}],
    )
    repository.replace_calls_for_api(
        conn, checkout_id, api_id,
        [{
            "to_service_name": "payments-service", "call_kind": "http",
            "reason": "authorize the pix payment for the order", "data_needed": ["amount", "pix_key"],
            "purpose_kind": "data_fetch", "confidence": 0.9,
        }],
        [{"file": "checkout.py", "start_line": 1, "end_line": 20}],
    )
    repository.reconcile_service_call_targets(conn)

    order_api_id = repository.upsert_api(conn, order_id, "POST", "/orders", "creates order", "desc", [], [])
    repository.replace_calls_for_api(
        conn, order_id, order_api_id,
        [
            {
                "to_service_name": "payments-service", "call_kind": "http",
                "reason": "check payment confirmation status", "data_needed": ["order_id"],
                "purpose_kind": "data_fetch", "confidence": 0.7,
            },
            {
                # Looks internal (shares the "-service" naming convention) but was
                # never indexed — should surface as an unmapped_internal_hint.
                "to_service_name": "shipping-service", "call_kind": "http",
                "reason": "schedule delivery once the order is confirmed", "data_needed": ["order_id"],
                "purpose_kind": "other", "confidence": 0.6, "target_kind": "unknown",
            },
        ],
        [],
    )
    repository.reconcile_service_call_targets(conn)

    payments_api_id = repository.upsert_api(conn, payments_id, "POST", "/charge", "charges a card", "desc", [], [])
    repository.replace_calls_for_api(
        conn, payments_id, payments_api_id,
        [{
            # A genuine external integration — should surface as external_integrations.
            "to_service_name": "Stripe API", "call_kind": "http",
            "reason": "charge the customer's card via the vendor gateway", "data_needed": ["amount", "pix_key"],
            "purpose_kind": "data_fetch", "confidence": 0.85, "target_kind": "external",
        }],
        [],
    )
    repository.reconcile_service_call_targets(conn)

    # notification-service is only reachable via a message link off payments-service —
    # exercises the same "connected but unlikely to change" shape as the user's own
    # example (order events reaching a notifier that has nothing to do with Pix).
    repository.replace_messages(
        conn, payments_id,
        [{"direction": "publishes", "channel": "payment_authorized", "shape_json": [], "description": "d"}], [],
    )
    repository.replace_messages(
        conn, notif_id,
        [{"direction": "consumes", "channel": "payment_authorized", "shape_json": [], "description": "d"}], [],
    )
    repository.rebuild_search_index(conn)
    return conn


def test_no_matching_service_short_circuits_without_calling_backend(tmp_path: Path):
    conn = open_db(tmp_path / "empty.db")
    backend = FakeBackend({"primary": [], "secondary": [], "no_change": []})

    result = change_surface.analyze_change_surface(conn, "completely unrelated task xyz", backend)

    assert result["primary"] == []
    assert backend.calls == 0
    assert "note" in result


def test_hallucinated_service_is_filtered_and_evidence_confidence_attached(tmp_path: Path):
    conn = _build_pix_fixture(tmp_path / "pix.db")
    backend = FakeBackend({
        "primary": [
            {"service": "checkout-service", "reason": "owns checkout entry point", "confidence": 0.95},
            {"service": "payments-service", "reason": "owns payment method resolution", "confidence": 0.9},
            {"service": "a-service-that-does-not-exist", "reason": "hallucinated", "confidence": 0.5},
        ],
        "secondary": [
            {"service": "order-service", "reason": "consumes payment confirmation", "confidence": 0.4},
        ],
        "no_change": [
            {"service": "notification-service", "reason": "unrelated to payments", "confidence": 0.8},
        ],
    })

    result = change_surface.analyze_change_surface(conn, "Add support for Pix in checkout", backend)

    assert backend.calls == 1
    primary_names = {f["service"] for f in result["primary"]}
    assert primary_names == {"checkout-service", "payments-service"}  # hallucinated entry dropped
    checkout_finding = next(f for f in result["primary"] if f["service"] == "checkout-service")
    assert checkout_finding["confidence"] == 0.95
    assert checkout_finding["evidence"]  # evidence carried through from indexed apis/calls

    secondary_names = {f["service"] for f in result["secondary"]}
    assert secondary_names == {"order-service"}

    no_change_names = {f["service"] for f in result["no_change_hint"]}
    assert no_change_names == {"notification-service"}

    flow_pairs = {(f["from"], f["to"]) for f in result["flow"]}
    assert ("checkout-service", "payments-service") in flow_pairs


def test_external_and_unmapped_internal_buckets_are_populated(tmp_path: Path):
    conn = _build_pix_fixture(tmp_path / "pix3.db")
    backend = FakeBackend({
        "primary": [
            {"service": "checkout-service", "reason": "owns checkout entry point", "confidence": 0.95},
            {"service": "payments-service", "reason": "owns payment method resolution", "confidence": 0.9},
        ],
        "secondary": [
            {"service": "order-service", "reason": "consumes payment confirmation", "confidence": 0.4},
        ],
        "no_change": [
            {"service": "notification-service", "reason": "unrelated to payments", "confidence": 0.8},
        ],
    })

    result = change_surface.analyze_change_surface(conn, "Add support for Pix in checkout", backend)

    external = {f["service"] for f in result["external_integrations"]}
    assert external == {"Stripe API"}  # only surfaced because payments-service is primary
    stripe_finding = next(f for f in result["external_integrations"] if f["service"] == "Stripe API")
    assert stripe_finding["via_service"] == "payments-service"
    assert stripe_finding["confidence"] == 0.85

    unmapped = {f["service"] for f in result["unmapped_internal_hint"]}
    assert unmapped == {"shipping-service"}  # only surfaced because order-service is secondary
    shipping_finding = next(f for f in result["unmapped_internal_hint"] if f["service"] == "shipping-service")
    assert shipping_finding["via_service"] == "order-service"


def test_analyze_change_surface_persists_a_run_and_returns_its_id(tmp_path: Path):
    conn = _build_pix_fixture(tmp_path / "pix4.db")
    backend = FakeBackend({
        "primary": [{"service": "checkout-service", "reason": "owns checkout entry point", "confidence": 0.95}],
        "secondary": [], "no_change": [],
    })

    result = change_surface.analyze_change_surface(conn, "Add support for Pix in checkout", backend)

    assert "run_id" in result
    findings = repository.list_change_surface_findings(conn, result["run_id"])
    assert any(f["service"] == "checkout-service" and f["role"] == "primary" for f in findings)


def test_confidence_is_recalibrated_from_historical_feedback(tmp_path: Path):
    conn = _build_pix_fixture(tmp_path / "pix5.db")
    backend = FakeBackend({
        "primary": [{"service": "checkout-service", "reason": "owns checkout entry point", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    })

    # No history yet -> confidence passes through unchanged.
    first = change_surface.analyze_change_surface(conn, "Add support for Pix in checkout", backend)
    assert first["primary"][0]["confidence"] == 0.9

    # Record enough "rejected" feedback for checkout-service that future runs
    # should be nudged down, even though the LLM keeps saying 0.9.
    run_id = first["run_id"]
    for _ in range(4):
        repository.record_change_surface_feedback(conn, run_id, "checkout-service", "rejected")

    second = change_surface.analyze_change_surface(conn, "Add support for Pix in checkout", backend)
    assert second["primary"][0]["confidence"] < 0.9


def test_hint_services_are_included_even_without_keyword_match(tmp_path: Path):
    conn = _build_pix_fixture(tmp_path / "pix2.db")
    backend = FakeBackend({
        "primary": [{"service": "notification-service", "reason": "explicitly hinted", "confidence": 0.6}],
        "secondary": [], "no_change": [],
    })

    result = change_surface.analyze_change_surface(
        conn, "xyz unrelated text", backend, hint_services=["notification-service"]
    )

    assert backend.calls == 1
    assert {f["service"] for f in result["primary"]} == {"notification-service"}
