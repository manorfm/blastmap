from pathlib import Path

from impactmesh.db.connection import open_db
from impactmesh.db.repositories import components as components_repo
from impactmesh.db.repositories import services as services_repo
from impactmesh.mcp import queries


def test_describe_service_includes_its_components(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = services_repo.ensure_service(conn, "orders-service", "/tmp/orders", "python")
    components_repo.upsert_component(
        conn, service_id, "OrdersController", "orders/controller.py",
        "Handles order creation and lookup.", [],
    )

    result = queries.describe_service(conn, "orders-service")

    assert result["components"] == [
        {"name": "OrdersController", "file_path": "orders/controller.py", "summary": "Handles order creation and lookup."}
    ]
