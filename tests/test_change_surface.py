"""Unit tests for generation.change_surface, the task-to-service impact analysis
that backs the find_change_surface MCP tool. The LLM backend is faked so these run
deterministically without a real subprocess call — matching the harness's own
"no tool/file access, evidence given in the prompt" contract.
"""
from pathlib import Path

from orbitkb.db.connection import open_db
from orbitkb.db.repositories import apis as apis_repo
from orbitkb.db.repositories import change_surface as change_surface_repo
from orbitkb.db.repositories import messages as messages_repo
from orbitkb.db.repositories import persistence as persistence_repo
from orbitkb.db.repositories import search as search_repo
from orbitkb.db.repositories import service_calls as service_calls_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.generation import change_surface
from orbitkb.generation.backend_base import GenerationOutcome


class FakeBackend:
    name = "fake"

    def __init__(self, response: dict):
        self.response = response
        self.calls = 0

    def generate(self, prompt: str, schema: dict, cwd: Path) -> GenerationOutcome:
        self.calls += 1
        return GenerationOutcome(structured=self.response)


def _build_pix_fixture(db_path: Path):
    conn = open_db(db_path)
    checkout_id = services_repo.ensure_service(conn, "checkout-service", "/tmp/checkout", "python")
    payments_id = services_repo.ensure_service(conn, "payments-service", "/tmp/payments", "node-ts")
    order_id = services_repo.ensure_service(conn, "order-service", "/tmp/order", "python")
    notif_id = services_repo.ensure_service(conn, "notification-service", "/tmp/notif", "python")

    services_repo.update_service_overview(conn, checkout_id, "Owns the checkout entry point and forwards payment method.", "L")
    services_repo.update_service_overview(conn, payments_id, "Owns payment method resolution and payment authorization.", "L")
    services_repo.update_service_overview(conn, order_id, "Consumes payment confirmation to create orders.", "L")
    services_repo.update_service_overview(conn, notif_id, "Sends emails when an order ships.", "L")

    api_id = apis_repo.upsert_api(
        conn, checkout_id, "POST", "/checkout", "starts checkout", "desc", [],
        [{"file": "checkout.py", "start_line": 1, "end_line": 20}],
    )
    service_calls_repo.replace_calls_for_api(
        conn, checkout_id, api_id,
        [{
            "to_service_name": "payments-service", "call_kind": "http",
            "reason": "authorize the pix payment for the order", "data_needed": ["amount", "pix_key"],
            "purpose_kind": "data_fetch", "confidence": 0.9,
        }],
        [{"file": "checkout.py", "start_line": 1, "end_line": 20}],
    )
    service_calls_repo.reconcile_service_call_targets(conn)

    order_api_id = apis_repo.upsert_api(conn, order_id, "POST", "/orders", "creates order", "desc", [], [])
    service_calls_repo.replace_calls_for_api(
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
    service_calls_repo.reconcile_service_call_targets(conn)

    payments_api_id = apis_repo.upsert_api(conn, payments_id, "POST", "/charge", "charges a card", "desc", [], [])
    service_calls_repo.replace_calls_for_api(
        conn, payments_id, payments_api_id,
        [{
            # A genuine external integration — should surface as external_integrations.
            "to_service_name": "Stripe API", "call_kind": "http",
            "reason": "charge the customer's card via the vendor gateway", "data_needed": ["amount", "pix_key"],
            "purpose_kind": "data_fetch", "confidence": 0.85, "target_kind": "external",
        }],
        [],
    )
    service_calls_repo.reconcile_service_call_targets(conn)

    # notification-service is only reachable via a message link off payments-service —
    # exercises the same "connected but unlikely to change" shape as the user's own
    # example (order events reaching a notifier that has nothing to do with Pix).
    messages_repo.replace_messages(
        conn, payments_id,
        [{"direction": "publishes", "channel": "payment_authorized", "shape_json": [], "description": "d"}], [],
    )
    messages_repo.replace_messages(
        conn, notif_id,
        [{"direction": "consumes", "channel": "payment_authorized", "shape_json": [], "description": "d"}], [],
    )
    search_repo.rebuild_search_index(conn)
    return conn


def test_no_matching_service_short_circuits_without_calling_backend(tmp_path: Path):
    conn = open_db(tmp_path / "empty.db")
    backend = FakeBackend({"primary": [], "secondary": [], "no_change": []})

    result = change_surface.analyze_change_surface(conn, "completely unrelated task xyz", backend)

    assert result["primary"] == []
    assert backend.calls == 0
    assert "note" in result
    assert len(result["unknowns"]) == 1
    assert result["unknowns"][0]["status"] == "unknown"
    assert "suggestion" in result["unknowns"][0]


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

    assert any(r["tool"] == "describe_api" and r["arguments"]["service"] == "checkout-service" for r in result["recommended_next_queries"])


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

    unknown_names = {u["service"] for u in result["unknowns"]}
    assert unknown_names == {"shipping-service"}
    shipping_unknown = next(u for u in result["unknowns"] if u["service"] == "shipping-service")
    assert shipping_unknown["status"] == "unknown"
    assert "suggestion" in shipping_unknown


def test_persistence_affected_lists_entities_owned_by_relevant_services(tmp_path: Path):
    conn = _build_pix_fixture(tmp_path / "pix7.db")
    payments_id = services_repo.get_service_by_name(conn, "payments-service")["id"]
    order_id = services_repo.get_service_by_name(conn, "order-service")["id"]
    persistence_repo.replace_persistence_entities(
        conn, payments_id, [{"name": "payment_method", "kind": "sql_table", "schema_json": []}],
        [{"file": "payments/models.py", "start_line": 1, "end_line": 10}],
    )
    persistence_repo.replace_persistence_entities(
        conn, order_id, [{"name": "orders", "kind": "sql_table", "schema_json": []}], [],
    )
    backend = FakeBackend({
        "primary": [{"service": "payments-service", "reason": "owns payment method resolution", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    })

    result = change_surface.analyze_change_surface(conn, "Add support for Pix in checkout", backend)

    persisted = {p["entity"]: p for p in result["persistence_affected"]}
    assert "payment_method" in persisted
    assert persisted["payment_method"]["service"] == "payments-service"
    assert persisted["payment_method"]["evidence"]
    # order-service is neither primary nor secondary here, so its entity is out of scope.
    assert "orders" not in persisted


def test_contracts_at_risk_lists_consumers_of_a_relevant_service_events(tmp_path: Path):
    conn = _build_pix_fixture(tmp_path / "pix6.db")
    backend = FakeBackend({
        "primary": [
            {"service": "checkout-service", "reason": "owns checkout entry point", "confidence": 0.95},
            {"service": "payments-service", "reason": "owns payment method resolution", "confidence": 0.9},
        ],
        "secondary": [], "no_change": [],
    })

    result = change_surface.analyze_change_surface(conn, "Add support for Pix in checkout", backend)

    contracts = {c["contract"]: c for c in result["contracts_at_risk"]}
    assert "payment_authorized" in contracts
    payment_authorized = contracts["payment_authorized"]
    assert payment_authorized["producer"] == "payments-service"
    assert payment_authorized["consumers"] == ["notification-service"]
    assert "potentially affected" in payment_authorized["reason"] or "requires verification" in payment_authorized["reason"]


def test_analyze_change_surface_persists_a_run_and_returns_its_id(tmp_path: Path):
    conn = _build_pix_fixture(tmp_path / "pix4.db")
    backend = FakeBackend({
        "primary": [{"service": "checkout-service", "reason": "owns checkout entry point", "confidence": 0.95}],
        "secondary": [], "no_change": [],
    })

    result = change_surface.analyze_change_surface(conn, "Add support for Pix in checkout", backend)

    assert "run_id" in result
    findings = change_surface_repo.list_change_surface_findings(conn, result["run_id"])
    assert any(f["service"] == "checkout-service" and f["role"] == "primary" for f in findings)
    # FakeBackend never reports usage — stays honestly None, not fabricated as 0.
    assert result["run_cost_usd"] is None
    run = change_surface_repo.get_change_surface_run(conn, result["run_id"])
    assert run["cost_usd"] is None


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
        change_surface_repo.record_change_surface_feedback(conn, run_id, "checkout-service", "rejected")

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
