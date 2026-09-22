import sqlite3
from pathlib import Path

import pytest

from orbitkb.db.connection import open_db


def test_static_cloud_facts_table_has_expected_columns(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(static_cloud_facts)")}
    assert columns == {
        "id", "service_id", "provider", "resource_type", "service_name", "operation",
        "operation_kind", "sdk", "target_name", "file_path", "start_line", "end_line", "updated_at",
    }


def test_cloud_iac_resources_table_has_expected_columns(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(cloud_iac_resources)")}
    assert columns == {
        "id", "repository_id", "service_id", "provider", "resource_type", "iac_resource_type",
        "logical_name", "physical_name", "source_format", "confidence", "file_path",
        "start_line", "end_line", "updated_at",
    }


def test_static_cloud_facts_rejects_unknown_provider(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO services (name, root_path, stack, updated_at) VALUES ('orders', '/tmp/orders', 'node-ts', '2026-01-01')"
    )
    service_id = conn.execute("SELECT id FROM services WHERE name = 'orders'").fetchone()["id"]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO static_cloud_facts
               (service_id, provider, resource_type, service_name, operation, operation_kind,
                sdk, target_name, file_path, start_line, end_line, updated_at)
               VALUES (?, 'gcp', 'queue', 'pubsub', 'Publish', 'publish', 'test-sdk', NULL,
                       'main.ts', 1, 1, '2026-01-01')""",
            (service_id,),
        )


def test_cloud_iac_resources_allows_null_service_id_for_repository_scoped_infra(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO repositories (name, root_path, updated_at) VALUES ('shop', '/tmp/shop', '2026-01-01')"
    )
    repository_id = conn.execute("SELECT id FROM repositories WHERE name = 'shop'").fetchone()["id"]
    conn.execute(
        """INSERT INTO cloud_iac_resources
           (repository_id, service_id, provider, resource_type, iac_resource_type, logical_name,
            physical_name, source_format, confidence, file_path, start_line, end_line, updated_at)
           VALUES (?, NULL, 'aws', 'queue', 'aws_sqs_queue', 'orders', 'orders-queue',
                   'terraform', 'high', 'infra/main.tf', 3, 6, '2026-01-01')""",
        (repository_id,),
    )
    conn.commit()

    row = conn.execute("SELECT service_id, physical_name FROM cloud_iac_resources").fetchone()
    assert row["service_id"] is None
    assert row["physical_name"] == "orders-queue"
