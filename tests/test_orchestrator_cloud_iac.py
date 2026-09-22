"""Verifies orbitkb.generation.orchestrator.index_path wires the repository-wide
IaC scan (orbitkb/iac/scanner.py) into the real indexing pipeline: scanned once
per repository, after every candidate service exists, so a resource can be
attributed to a real service_id — see plan.md's WP3 acceptance criterion.
"""
from pathlib import Path

from orbitkb.db.connection import open_db
from orbitkb.db.repositories import cloud_iac as cloud_iac_repo
from orbitkb.db.repositories import repositories as repositories_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.generation.orchestrator import index_path
from tests.test_orchestrator import FakeOrchestratorBackend


def _write_node_service(root: Path, name: str) -> None:
    service = root / name
    service.mkdir(parents=True)
    (service / "package.json").write_text('{"scripts": {"start": "node index.js"}}')
    (service / "index.js").write_text("// entrypoint\n")


def test_terraform_outside_any_service_root_is_stored_repository_scoped(tmp_path: Path):
    _write_node_service(tmp_path, "orders-service")
    infra = tmp_path / "infra"
    infra.mkdir()
    (infra / "main.tf").write_text(
        'resource "aws_sqs_queue" "orders" {\n  name = "orders-queue"\n}\n'
    )

    conn = open_db(tmp_path / "test.db")
    index_path(conn, tmp_path, FakeOrchestratorBackend())

    repository_id = repositories_repo.list_repositories(conn)[0]["id"]
    rows = cloud_iac_repo.list_iac_resources_for_repository(conn, repository_id)
    assert len(rows) == 1
    assert rows[0]["service_id"] is None
    assert rows[0]["physical_name"] == "orders-queue"


def test_terraform_inside_a_service_root_is_attributed_to_its_real_service_id(tmp_path: Path):
    _write_node_service(tmp_path, "orders-service")
    infra = tmp_path / "orders-service" / "infra"
    infra.mkdir(parents=True)
    (infra / "main.tf").write_text(
        'resource "aws_sqs_queue" "orders" {\n  name = "orders-queue"\n}\n'
    )

    conn = open_db(tmp_path / "test.db")
    index_path(conn, tmp_path, FakeOrchestratorBackend())

    orders = services_repo.get_service_by_name(conn, "orders-service")
    rows = cloud_iac_repo.list_iac_resources_for_service(conn, orders["id"])
    assert len(rows) == 1
    assert rows[0]["physical_name"] == "orders-queue"
