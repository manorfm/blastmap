"""Repository round-trip tests for db.repositories.embeddings: the two 'vector
storage' tables (service_embeddings, change_surface_run_embeddings) share one
module since they're the same concern for two different aggregates."""
import json
from pathlib import Path

from impactmesh.db.connection import open_db
from impactmesh.db.repositories import change_surface as change_surface_repo
from impactmesh.db.repositories import embeddings as repository
from impactmesh.db.repositories import services as services_repo

SAMPLE_RESULT = {
    "primary": [], "secondary": [], "no_change_hint": [], "external_integrations": [], "unmapped_internal_hint": [],
}


def test_service_embedding_round_trips(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = services_repo.ensure_service(conn, "orders-service", "/tmp/orders", "python")

    repository.upsert_service_embedding(conn, service_id, "stub-model", [0.1, 0.2, 0.3])

    rows = repository.get_all_service_embeddings(conn)
    assert len(rows) == 1
    assert rows[0]["service_id"] == service_id
    assert rows[0]["service_name"] == "orders-service"
    assert json.loads(rows[0]["vector_json"]) == [0.1, 0.2, 0.3]


def test_service_embedding_upsert_replaces_the_previous_vector(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = services_repo.ensure_service(conn, "orders-service", "/tmp/orders", "python")
    repository.upsert_service_embedding(conn, service_id, "stub-model", [0.1])

    repository.upsert_service_embedding(conn, service_id, "stub-model-v2", [0.9])

    rows = repository.get_all_service_embeddings(conn)
    assert len(rows) == 1
    assert json.loads(rows[0]["vector_json"]) == [0.9]


def test_delete_service_embedding_removes_it(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = services_repo.ensure_service(conn, "orders-service", "/tmp/orders", "python")
    repository.upsert_service_embedding(conn, service_id, "stub-model", [0.1])

    repository.delete_service_embedding(conn, service_id)

    assert repository.get_all_service_embeddings(conn) == []


def test_change_surface_run_embedding_round_trips(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    run_id = change_surface_repo.record_change_surface_run(conn, "Add Pix support", "claude", SAMPLE_RESULT)

    repository.upsert_change_surface_run_embedding(conn, run_id, "stub-model", [0.4, 0.5])

    rows = repository.get_all_change_surface_run_embeddings(conn)
    assert len(rows) == 1
    assert rows[0]["run_id"] == run_id
    assert json.loads(rows[0]["vector_json"]) == [0.4, 0.5]


def test_get_all_change_surface_run_embeddings_can_exclude_one_run(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    run1 = change_surface_repo.record_change_surface_run(conn, "task one", "claude", SAMPLE_RESULT)
    run2 = change_surface_repo.record_change_surface_run(conn, "task two", "claude", SAMPLE_RESULT)
    repository.upsert_change_surface_run_embedding(conn, run1, "stub-model", [0.1])
    repository.upsert_change_surface_run_embedding(conn, run2, "stub-model", [0.2])

    rows = repository.get_all_change_surface_run_embeddings(conn, exclude_run_id=run2)

    assert {r["run_id"] for r in rows} == {run1}
