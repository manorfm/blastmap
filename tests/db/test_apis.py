from pathlib import Path

from context_insight.db.connection import open_db
from context_insight.db.repositories import apis as apis_repo
from context_insight.db.repositories import services as services_repo


def test_prune_apis_not_in(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = services_repo.ensure_service(conn, "svc", "/tmp/svc", "python")
    apis_repo.upsert_api(conn, service_id, "GET", "/a", "s", "d", [], [])
    apis_repo.upsert_api(conn, service_id, "GET", "/b", "s", "d", [], [])
    assert len(apis_repo.list_apis(conn, service_id)) == 2

    apis_repo.prune_apis_not_in(conn, service_id, {("GET", "/a")})
    remaining = apis_repo.list_apis(conn, service_id)
    assert len(remaining) == 1
    assert remaining[0]["path"] == "/a"


def test_upsert_api_stores_request_shape(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = services_repo.ensure_service(conn, "svc", "/tmp/svc", "python")
    request_shape = [{"field": "amount", "type_desc": "number, amount in cents", "required": True}]

    api_id = apis_repo.upsert_api(conn, service_id, "POST", "/charge", "s", "d", [], [], request_shape=request_shape)

    row = apis_repo.get_api_by_key(conn, service_id, "POST", "/charge")
    assert row["id"] == api_id
    assert row["request_shape"] is not None


def test_upsert_api_defaults_request_shape_to_empty_when_omitted(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = services_repo.ensure_service(conn, "svc", "/tmp/svc", "python")

    apis_repo.upsert_api(conn, service_id, "GET", "/a", "s", "d", [], [])

    row = apis_repo.get_api_by_key(conn, service_id, "GET", "/a")
    assert row["request_shape"] == "[]"
