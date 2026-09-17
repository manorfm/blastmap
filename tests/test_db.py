from pathlib import Path

from context_insight.db import repository
from context_insight.db.connection import open_db

EVIDENCE = [{"file": "main.py", "start_line": 10, "end_line": 20}]


def test_schema_initializes(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    assert row["value"] == "2"


def test_ensure_service_and_overview(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = repository.ensure_service(conn, "orders-service", "/tmp/orders", "python")
    assert service_id > 0

    same_id = repository.ensure_service(conn, "orders-service", "/tmp/orders-moved", "python")
    assert same_id == service_id

    repository.update_service_overview(conn, service_id, "Handles orders.", "Longer description.")
    row = repository.get_service_by_name(conn, "orders-service")
    assert row["short_desc"] == "Handles orders."
    assert row["root_path"] == "/tmp/orders-moved"


def test_ensure_repository_links_to_service(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    repo_id = repository.ensure_repository(conn, "checkout-repo", "/tmp/checkout-repo")
    assert repo_id > 0

    same_repo_id = repository.ensure_repository(conn, "checkout-repo-renamed", "/tmp/checkout-repo")
    assert same_repo_id == repo_id  # keyed by root_path, name update is allowed

    service_id = repository.ensure_service(conn, "checkout-service", "/tmp/checkout-repo/checkout", "python", repository_id=repo_id)
    row = repository.get_service_by_name(conn, "checkout-service")
    assert row["repository_id"] == repo_id

    # Re-ensuring without a repository_id must not null out the existing link.
    repository.ensure_service(conn, "checkout-service", "/tmp/checkout-repo/checkout", "python")
    row = repository.get_service_by_name(conn, "checkout-service")
    assert row["repository_id"] == repo_id


def test_api_and_call_reconciliation(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    orders_id = repository.ensure_service(conn, "orders-service", "/tmp/orders", "python")
    api_id = repository.upsert_api(
        conn, orders_id, "POST", "/orders", "creates an order", "desc",
        [{"field": "order_id", "type_desc": "string"}], EVIDENCE,
    )
    repository.replace_calls_for_api(
        conn, orders_id, api_id,
        [{
            "to_service_name": "payments-service", "call_kind": "http", "reason": "charge card",
            "data_needed": ["amount"], "purpose_kind": "data_fetch", "confidence": 0.9,
        }],
        EVIDENCE,
    )

    calls = repository.list_calls_for_api(conn, api_id)
    assert len(calls) == 1
    assert calls[0]["to_service_name"] == "payments-service"
    assert calls[0]["confidence"] == 0.9
    assert calls[0]["evidence_json"] is not None

    row = conn.execute("SELECT to_service_id FROM service_calls WHERE from_api_id = ?", (api_id,)).fetchone()
    assert row["to_service_id"] is None  # payments-service not indexed yet

    payments_id = repository.ensure_service(conn, "payments-service", "/tmp/payments", "node-ts")
    repository.reconcile_service_call_targets(conn)

    row = conn.execute("SELECT to_service_id FROM service_calls WHERE from_api_id = ?", (api_id,)).fetchone()
    assert row["to_service_id"] == payments_id


def test_inbound_calls(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    checkout_id = repository.ensure_service(conn, "checkout-service", "/tmp/checkout", "python")
    payments_id = repository.ensure_service(conn, "payments-service", "/tmp/payments", "node-ts")
    api_id = repository.upsert_api(conn, checkout_id, "POST", "/checkout", "s", "d", [], EVIDENCE)
    repository.replace_calls_for_api(
        conn, checkout_id, api_id,
        [{
            "to_service_name": "payments-service", "call_kind": "http", "reason": "authorize payment",
            "data_needed": ["amount"], "purpose_kind": "data_fetch", "confidence": 0.8,
        }],
        EVIDENCE,
    )

    inbound = repository.list_inbound_calls(conn, payments_id)
    assert len(inbound) == 1
    assert inbound[0]["from_service_name"] == "checkout-service"
    assert inbound[0]["reason"] == "authorize payment"

    outbound_from_payments = repository.list_inbound_calls(conn, checkout_id)
    assert outbound_from_payments == []


def test_message_links(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    orders_id = repository.ensure_service(conn, "orders-service", "/tmp/orders", "python")
    notif_id = repository.ensure_service(conn, "notification-service", "/tmp/notif", "python")

    repository.replace_messages(
        conn, orders_id, [{"direction": "publishes", "channel": "order_created", "shape_json": {}, "description": "d"}], EVIDENCE,
    )
    repository.replace_messages(
        conn, notif_id, [{"direction": "consumes", "channel": "order_created", "shape_json": {}, "description": "d"}], EVIDENCE,
    )

    links = repository.list_message_links(conn, orders_id)
    assert len(links) == 1
    assert links[0]["channel"] == "order_created"
    assert links[0]["local_direction"] == "publishes"
    assert links[0]["other_service"] == "notification-service"

    reverse_links = repository.list_message_links(conn, notif_id)
    assert len(reverse_links) == 1
    assert reverse_links[0]["local_direction"] == "consumes"
    assert reverse_links[0]["other_service"] == "orders-service"


def test_persistence_and_messages_store_evidence(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = repository.ensure_service(conn, "orders-service", "/tmp/orders", "python")
    repository.replace_persistence_entities(
        conn, service_id, [{"name": "orders", "kind": "sql_table", "schema_json": []}], EVIDENCE,
    )
    repository.replace_messages(
        conn, service_id, [{"direction": "publishes", "channel": "order_created", "shape_json": [], "description": "d"}], EVIDENCE,
    )

    entities = repository.list_persistence(conn, service_id)
    assert entities[0]["evidence_json"] == '[{"file": "main.py", "start_line": 10, "end_line": 20}]'

    messages = repository.list_messages(conn, service_id)
    assert messages[0]["evidence_json"] == '[{"file": "main.py", "start_line": 10, "end_line": 20}]'


def test_prune_apis_not_in(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = repository.ensure_service(conn, "svc", "/tmp/svc", "python")
    repository.upsert_api(conn, service_id, "GET", "/a", "s", "d", [], [])
    repository.upsert_api(conn, service_id, "GET", "/b", "s", "d", [], [])
    assert len(repository.list_apis(conn, service_id)) == 2

    repository.prune_apis_not_in(conn, service_id, {("GET", "/a")})
    remaining = repository.list_apis(conn, service_id)
    assert len(remaining) == 1
    assert remaining[0]["path"] == "/a"


def test_search_finds_service_api_and_relationship(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    checkout_id = repository.ensure_service(conn, "checkout-service", "/tmp/checkout", "python")
    payments_id = repository.ensure_service(conn, "payments-service", "/tmp/payments", "node-ts")
    repository.update_service_overview(conn, payments_id, "Charges customer cards.", "Longer.")
    repository.update_service_overview(conn, checkout_id, "Checkout flow.", "Longer.")
    api_id = repository.upsert_api(conn, payments_id, "POST", "/charge", "charges a card", "desc", [], [])
    repository.upsert_api(conn, checkout_id, "POST", "/checkout", "starts checkout", "desc", [], [])
    repository.replace_calls_for_api(
        conn, checkout_id, api_id,
        [{
            "to_service_name": "payments-service", "call_kind": "http",
            "reason": "authorize the pix payment", "data_needed": [], "purpose_kind": "data_fetch",
        }],
        [],
    )

    by_service = repository.search(conn, "charge")
    assert "service" in {r["kind"] for r in by_service}
    assert "api" in {r["kind"] for r in by_service}

    by_reason = repository.search(conn, "pix")
    assert any(r["kind"] == "relationship" and r["service"] == "checkout-service" for r in by_reason)
