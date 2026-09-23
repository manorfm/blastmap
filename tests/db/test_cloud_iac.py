import json
from pathlib import Path

from orbitkb.db.connection import open_db
from orbitkb.db.repositories import cloud_iac as cloud_iac_repo
from orbitkb.db.repositories import repositories as repositories_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.iac.models import IacResource


def _resource(matched_service_name: str | None = None) -> IacResource:
    return IacResource(
        provider="aws", resource_type="queue", iac_resource_type="aws_sqs_queue",
        logical_name="orders", physical_name="orders-queue", source_format="terraform",
        confidence="high", file_path="infra/main.tf", start_line=1, end_line=3,
        matched_service_name=matched_service_name,
    )


def test_resource_matched_to_a_service_resolves_its_service_id(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    repository_id = repositories_repo.ensure_repository(conn, "shop", "/tmp/shop")
    service_id = services_repo.ensure_service(
        conn, "orders-service", "/tmp/shop/orders-service", "node-ts", repository_id=repository_id,
    )

    cloud_iac_repo.replace_iac_resources(conn, repository_id, [_resource(matched_service_name="orders-service")])

    rows = cloud_iac_repo.list_iac_resources_for_repository(conn, repository_id)
    assert rows[0]["service_id"] == service_id


def test_unmatched_resource_is_repository_scoped_with_null_service_id(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    repository_id = repositories_repo.ensure_repository(conn, "shop", "/tmp/shop")

    cloud_iac_repo.replace_iac_resources(conn, repository_id, [_resource(matched_service_name=None)])

    rows = cloud_iac_repo.list_iac_resources_for_repository(conn, repository_id)
    assert rows[0]["service_id"] is None


def test_replace_is_a_full_replacement_for_the_repository(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    repository_id = repositories_repo.ensure_repository(conn, "shop", "/tmp/shop")

    cloud_iac_repo.replace_iac_resources(conn, repository_id, [_resource()])
    cloud_iac_repo.replace_iac_resources(conn, repository_id, [])

    assert cloud_iac_repo.list_iac_resources_for_repository(conn, repository_id) == []


def test_list_iac_resources_for_service_filters_by_matched_service(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    repository_id = repositories_repo.ensure_repository(conn, "shop", "/tmp/shop")
    service_id = services_repo.ensure_service(
        conn, "orders-service", "/tmp/shop/orders-service", "node-ts", repository_id=repository_id,
    )

    cloud_iac_repo.replace_iac_resources(
        conn, repository_id,
        [_resource(matched_service_name="orders-service"), _resource(matched_service_name=None)],
    )

    rows = cloud_iac_repo.list_iac_resources_for_service(conn, service_id)
    assert len(rows) == 1
    assert rows[0]["service_id"] == service_id


def test_attributes_are_persisted_as_json(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    repository_id = repositories_repo.ensure_repository(conn, "shop", "/tmp/shop")
    resource = IacResource(
        provider="aws", resource_type="queue", iac_resource_type="aws_sqs_queue",
        logical_name="orders", physical_name="orders-queue", source_format="terraform",
        confidence="high", file_path="infra/main.tf", start_line=1, end_line=3,
        attributes={"redrive_policy": True, "acl": "public-read"},
    )

    cloud_iac_repo.replace_iac_resources(conn, repository_id, [resource])

    row = cloud_iac_repo.list_iac_resources_for_repository(conn, repository_id)[0]
    assert json.loads(row["attributes_json"]) == {"redrive_policy": True, "acl": "public-read"}


def test_attributes_default_to_an_empty_json_object(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    repository_id = repositories_repo.ensure_repository(conn, "shop", "/tmp/shop")

    cloud_iac_repo.replace_iac_resources(conn, repository_id, [_resource()])

    row = cloud_iac_repo.list_iac_resources_for_repository(conn, repository_id)[0]
    assert json.loads(row["attributes_json"]) == {}
