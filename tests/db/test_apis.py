from pathlib import Path

from blastmap.db.connection import open_db
from blastmap.db.repositories import apis as apis_repo
from blastmap.db.repositories import services as services_repo


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
