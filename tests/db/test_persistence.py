from pathlib import Path

from context_insight.db.connection import open_db
from context_insight.db.repositories import persistence as persistence_repo
from context_insight.db.repositories import services as services_repo

EVIDENCE = [{"file": "main.py", "start_line": 10, "end_line": 20}]


def test_persistence_stores_evidence(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = services_repo.ensure_service(conn, "orders-service", "/tmp/orders", "python")
    persistence_repo.replace_persistence_entities(
        conn, service_id, [{"name": "orders", "kind": "sql_table", "schema_json": []}], EVIDENCE,
    )

    entities = persistence_repo.list_persistence(conn, service_id)
    assert entities[0]["evidence_json"] == '[{"file": "main.py", "start_line": 10, "end_line": 20}]'
