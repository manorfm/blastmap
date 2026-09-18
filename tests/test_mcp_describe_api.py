"""Coverage for describe_api's request_shape field — mirrors the existing
response_shape treatment (see db/repositories/apis.py upsert_api)."""
from pathlib import Path

from blastmap.db.connection import open_db
from blastmap.db.repositories import apis as apis_repo
from blastmap.db.repositories import services as services_repo
from blastmap.mcp import queries


def test_describe_api_includes_request_shape(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = services_repo.ensure_service(conn, "payments-service", "/tmp/payments", "python")
    request_shape = [{"field": "amount", "type_desc": "number, amount in cents", "required": True}]
    apis_repo.upsert_api(
        conn, service_id, "POST", "/charge", "charges a card", "d", [], [], request_shape=request_shape,
    )

    result = queries.describe_api(conn, "payments-service", "POST", "/charge")

    assert result["request_shape"] == request_shape


def test_describe_api_defaults_request_shape_to_empty_list(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = services_repo.ensure_service(conn, "payments-service", "/tmp/payments", "python")
    apis_repo.upsert_api(conn, service_id, "POST", "/charge", "charges a card", "d", [], [])

    result = queries.describe_api(conn, "payments-service", "POST", "/charge")

    assert result["request_shape"] == []
