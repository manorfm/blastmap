from pathlib import Path

from blastmap.db.connection import open_db
from blastmap.db.repositories import apis as apis_repo
from blastmap.db.repositories import service_calls as service_calls_repo
from blastmap.db.repositories import services as services_repo

EVIDENCE = [{"file": "main.py", "start_line": 10, "end_line": 20}]


def test_api_and_call_reconciliation(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    orders_id = services_repo.ensure_service(conn, "orders-service", "/tmp/orders", "python")
    api_id = apis_repo.upsert_api(
        conn, orders_id, "POST", "/orders", "creates an order", "desc",
        [{"field": "order_id", "type_desc": "string"}], EVIDENCE,
    )
    service_calls_repo.replace_calls_for_api(
        conn, orders_id, api_id,
        [{
            "to_service_name": "payments-service", "call_kind": "http", "reason": "charge card",
            "data_needed": ["amount"], "purpose_kind": "data_fetch", "confidence": 0.9,
        }],
        EVIDENCE,
    )

    calls = service_calls_repo.list_calls_for_api(conn, api_id)
    assert len(calls) == 1
    assert calls[0]["to_service_name"] == "payments-service"
    assert calls[0]["confidence"] == 0.9
    assert calls[0]["evidence_json"] is not None

    row = conn.execute("SELECT to_service_id FROM service_calls WHERE from_api_id = ?", (api_id,)).fetchone()
    assert row["to_service_id"] is None  # payments-service not indexed yet

    payments_id = services_repo.ensure_service(conn, "payments-service", "/tmp/payments", "node-ts")
    service_calls_repo.reconcile_service_call_targets(conn)

    row = conn.execute("SELECT to_service_id FROM service_calls WHERE from_api_id = ?", (api_id,)).fetchone()
    assert row["to_service_id"] == payments_id


def test_target_kind_is_persisted_and_ground_truth_overrides_llm_guess(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    checkout_id = services_repo.ensure_service(conn, "checkout-service", "/tmp/checkout", "python")
    api_id = apis_repo.upsert_api(conn, checkout_id, "POST", "/checkout", "s", "d", [], EVIDENCE)
    service_calls_repo.replace_calls_for_api(
        conn, checkout_id, api_id,
        [
            {
                "to_service_name": "Stripe API", "call_kind": "http", "reason": "charge card",
                "data_needed": [], "purpose_kind": "data_fetch", "confidence": 0.9, "target_kind": "external",
            },
            {
                # LLM couldn't tell from a bare name with no "-service"-style suffix,
                # but this will actually resolve once "billing" is indexed below —
                # ground truth must win even though the naming heuristic alone
                # wouldn't have caught it.
                "to_service_name": "billing", "call_kind": "http", "reason": "authorize payment",
                "data_needed": [], "purpose_kind": "data_fetch", "confidence": 0.5, "target_kind": "unknown",
            },
            {
                # LLM couldn't tell, and this service is never indexed — the naming
                # heuristic should still recognize the shared "-service" suffix.
                "to_service_name": "shipping-service", "call_kind": "http", "reason": "schedule delivery",
                "data_needed": [], "purpose_kind": "other", "confidence": 0.3, "target_kind": "unknown",
            },
        ],
        EVIDENCE,
    )

    calls_before = {c["to_service_name"]: c["target_kind"] for c in service_calls_repo.list_calls_for_api(conn, api_id)}
    assert calls_before["Stripe API"] == "external"
    assert calls_before["billing"] == "unknown"  # not reconciled to a real service yet
    assert calls_before["shipping-service"] == "internal"  # naming heuristic already applied

    services_repo.ensure_service(conn, "billing", "/tmp/billing", "node-ts")
    service_calls_repo.reconcile_service_call_targets(conn)

    calls_after = {c["to_service_name"]: c["target_kind"] for c in service_calls_repo.list_calls_for_api(conn, api_id)}
    assert calls_after["Stripe API"] == "external"  # untouched
    assert calls_after["billing"] == "internal"  # ground truth: now resolved
    assert calls_after["shipping-service"] == "internal"  # unchanged, still a heuristic guess


def test_reconcile_scoped_to_one_service_leaves_other_services_rows_untouched(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    # "gatewaytarget" deliberately has no "-service"/"-svc" suffix, so the naming-
    # convention heuristic (discovery/integration_heuristics.py) can't jump in and
    # classify it before ground truth does — it must stay "unknown" until a real
    # service of that exact name is indexed.
    a_id = services_repo.ensure_service(conn, "a-service", "/tmp/a", "python")
    b_id = services_repo.ensure_service(conn, "b-service", "/tmp/b", "python")
    a_api = apis_repo.upsert_api(conn, a_id, "GET", "/a", "s", "d", [], EVIDENCE)
    b_api = apis_repo.upsert_api(conn, b_id, "GET", "/b", "s", "d", [], EVIDENCE)
    for service_id, api_id in ((a_id, a_api), (b_id, b_api)):
        service_calls_repo.replace_calls_for_api(
            conn, service_id, api_id,
            [{"to_service_name": "gatewaytarget", "call_kind": "http", "reason": "r",
              "data_needed": [], "purpose_kind": "other", "confidence": 0.5, "target_kind": "unknown"}],
            EVIDENCE,
        )
    # gatewaytarget didn't exist yet when either call was written, so both are dangling.

    services_repo.ensure_service(conn, "gatewaytarget", "/tmp/c", "python")
    service_calls_repo.reconcile_service_call_targets(conn, service_id=a_id)

    a_calls = {c["to_service_name"]: c["target_kind"] for c in service_calls_repo.list_calls_for_service(conn, a_id)}
    b_calls = {c["to_service_name"]: c["target_kind"] for c in service_calls_repo.list_calls_for_service(conn, b_id)}
    assert a_calls["gatewaytarget"] == "internal"  # scoped reconcile resolved a-service's own row
    assert b_calls["gatewaytarget"] == "unknown"  # untouched: b-service wasn't in scope

    service_calls_repo.reconcile_service_call_targets(conn)  # unscoped: resolves everyone

    b_calls_after = {c["to_service_name"]: c["target_kind"] for c in service_calls_repo.list_calls_for_service(conn, b_id)}
    assert b_calls_after["gatewaytarget"] == "internal"


def test_inbound_calls(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    checkout_id = services_repo.ensure_service(conn, "checkout-service", "/tmp/checkout", "python")
    payments_id = services_repo.ensure_service(conn, "payments-service", "/tmp/payments", "node-ts")
    api_id = apis_repo.upsert_api(conn, checkout_id, "POST", "/checkout", "s", "d", [], EVIDENCE)
    service_calls_repo.replace_calls_for_api(
        conn, checkout_id, api_id,
        [{
            "to_service_name": "payments-service", "call_kind": "http", "reason": "authorize payment",
            "data_needed": ["amount"], "purpose_kind": "data_fetch", "confidence": 0.8,
        }],
        EVIDENCE,
    )

    inbound = service_calls_repo.list_inbound_calls(conn, payments_id)
    assert len(inbound) == 1
    assert inbound[0]["from_service_name"] == "checkout-service"
    assert inbound[0]["reason"] == "authorize payment"

    outbound_from_payments = service_calls_repo.list_inbound_calls(conn, checkout_id)
    assert outbound_from_payments == []
