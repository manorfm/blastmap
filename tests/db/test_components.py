from pathlib import Path

from orbitkb.db.connection import open_db
from orbitkb.db.repositories import components as components_repo
from orbitkb.db.repositories import services as services_repo


def test_upsert_component_inserts_a_new_row(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = services_repo.ensure_service(conn, "orders-service", "/tmp/orders", "python")

    components_repo.upsert_component(
        conn, service_id, "OrdersController", "orders/controller.py",
        "Handles order creation and lookup.",
        [{"file": "orders/controller.py", "start_line": 1, "end_line": 40}],
    )

    rows = components_repo.list_components(conn, service_id)
    assert len(rows) == 1
    assert rows[0]["name"] == "OrdersController"
    assert rows[0]["summary"] == "Handles order creation and lookup."
    assert rows[0]["evidence_json"] == '[{"file": "orders/controller.py", "start_line": 1, "end_line": 40}]'


def test_upsert_component_updates_an_existing_row_in_place(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = services_repo.ensure_service(conn, "orders-service", "/tmp/orders", "python")
    components_repo.upsert_component(conn, service_id, "OrdersController", "orders/controller.py", "stale", [])

    components_repo.upsert_component(conn, service_id, "OrdersController", "orders/controller.py", "fresh", [])

    rows = components_repo.list_components(conn, service_id)
    assert len(rows) == 1
    assert rows[0]["summary"] == "fresh"


def test_prune_components_not_in_removes_stale_rows_only(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = services_repo.ensure_service(conn, "orders-service", "/tmp/orders", "python")
    components_repo.upsert_component(conn, service_id, "Keep", "keep.py", "s", [])
    components_repo.upsert_component(conn, service_id, "Drop", "drop.py", "s", [])

    components_repo.prune_components_not_in(conn, service_id, {("Keep", "keep.py")})

    rows = components_repo.list_components(conn, service_id)
    assert [r["name"] for r in rows] == ["Keep"]


def test_components_are_scoped_per_service(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    a = services_repo.ensure_service(conn, "orders-service", "/tmp/orders", "python")
    b = services_repo.ensure_service(conn, "payments-service", "/tmp/payments", "node-ts")

    components_repo.upsert_component(conn, a, "A", "a.py", "s", [])
    components_repo.upsert_component(conn, b, "B", "b.ts", "s", [])

    assert [r["name"] for r in components_repo.list_components(conn, a)] == ["A"]
    assert [r["name"] for r in components_repo.list_components(conn, b)] == ["B"]
