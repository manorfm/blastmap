from pathlib import Path

from context_insight.db import repository
from context_insight.db.connection import open_db


def test_schema_initializes(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    assert row["value"] == "1"


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


def test_api_and_call_reconciliation(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    orders_id = repository.ensure_service(conn, "orders-service", "/tmp/orders", "python")
    api_id = repository.upsert_api(
        conn, orders_id, "POST", "/orders", "creates an order", "desc", [{"field": "order_id", "type_desc": "string"}], ["main.py"]
    )
    repository.replace_calls_for_api(
        conn, orders_id, api_id,
        [{"to_service_name": "payments-service", "call_kind": "http", "reason": "charge card", "data_needed": ["amount"], "purpose_kind": "data_fetch"}],
    )

    calls = repository.list_calls_for_api(conn, api_id)
    assert len(calls) == 1
    assert calls[0]["to_service_name"] == "payments-service"

    row = conn.execute("SELECT to_service_id FROM service_calls WHERE from_api_id = ?", (api_id,)).fetchone()
    assert row["to_service_id"] is None  # payments-service not indexed yet

    payments_id = repository.ensure_service(conn, "payments-service", "/tmp/payments", "node-ts")
    repository.reconcile_service_call_targets(conn)

    row = conn.execute("SELECT to_service_id FROM service_calls WHERE from_api_id = ?", (api_id,)).fetchone()
    assert row["to_service_id"] == payments_id


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


def test_search_finds_service_and_api(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = repository.ensure_service(conn, "payments-service", "/tmp/payments", "node-ts")
    repository.update_service_overview(conn, service_id, "Charges customer cards.", "Longer.")
    repository.upsert_api(conn, service_id, "POST", "/charge", "charges a card", "desc", [], [])

    results = repository.search(conn, "charge")
    kinds = {r["kind"] for r in results}
    assert "service" in kinds
    assert "api" in kinds
