from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from orbitkb.analysis.models import AnalysisResult, CloudFact, Evidence
from orbitkb.db.connection import open_db
from orbitkb.db.repositories import cloud_iac as cloud_iac_repo
from orbitkb.db.repositories import flows as flows_repo
from orbitkb.db.repositories import repositories as repositories_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.iac.models import IacResource
from orbitkb.mcp import queries
from tests.mcp_test_helpers import content_json, server_params


def test_describe_cloud_dependencies_returns_static_facts_and_iac_resources(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    repository_id = repositories_repo.ensure_repository(conn, "shop", "/tmp/shop")
    service_id = services_repo.ensure_service(
        conn, "orders-service", "/tmp/shop/orders-service", "node-ts", repository_id=repository_id,
    )
    flows_repo.replace_analysis(conn, service_id, AnalysisResult(cloud_facts=[
        CloudFact(
            "aws", "queue", "sqs", "SendMessage", "publish", "aws-sdk-js-v3", None,
            Evidence("publisher.ts", 4, 4),
        ),
    ]))
    cloud_iac_repo.replace_iac_resources(conn, repository_id, [
        IacResource(
            provider="aws", resource_type="queue", iac_resource_type="aws_sqs_queue",
            logical_name="orders", physical_name="orders-queue", source_format="terraform",
            confidence="high", file_path="infra/main.tf", start_line=1, end_line=3,
            matched_service_name="orders-service",
        ),
    ])

    result = queries.describe_cloud_dependencies(conn, "orders-service")

    assert result["service"] == "orders-service"
    assert result["repository"] == "shop"
    assert result["static_facts"] == [{
        "provider": "aws", "resource_type": "queue", "service_name": "sqs",
        "operation": "SendMessage", "operation_kind": "publish", "sdk": "aws-sdk-js-v3",
        "target_name": None,
        "evidence": {"file": "publisher.ts", "start_line": 4, "end_line": 4},
    }]
    assert result["iac_resources"] == [{
        "provider": "aws", "resource_type": "queue", "iac_resource_type": "aws_sqs_queue",
        "logical_name": "orders", "physical_name": "orders-queue", "source_format": "terraform",
        "confidence": "high",
        "evidence": {"file": "infra/main.tf", "start_line": 1, "end_line": 3},
    }]
    assert result["pagination"]["static_facts"]["total"] == 1
    assert result["pagination"]["iac_resources"]["total"] == 1


def test_describe_cloud_dependencies_on_unknown_service_returns_error(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")

    result = queries.describe_cloud_dependencies(conn, "ghost-service")

    assert "error" in result


def test_describe_cloud_dependencies_paginates_each_list_independently(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    repository_id = repositories_repo.ensure_repository(conn, "shop", "/tmp/shop")
    service_id = services_repo.ensure_service(
        conn, "orders-service", "/tmp/shop/orders-service", "node-ts", repository_id=repository_id,
    )
    flows_repo.replace_analysis(conn, service_id, AnalysisResult(cloud_facts=[
        CloudFact("aws", "queue", "sqs", "SendMessage", "publish", "aws-sdk-js-v3", None, Evidence("a.ts", 1, 1)),
        CloudFact("aws", "object_storage", "s3", "PutObject", "write", "aws-sdk-js-v3", None, Evidence("b.ts", 2, 2)),
    ]))

    result = queries.describe_cloud_dependencies(conn, "orders-service", limit=1)

    assert len(result["static_facts"]) == 1
    assert result["pagination"]["static_facts"] == {"total": 2, "truncated": True}


@pytest.mark.anyio
async def test_describe_cloud_dependencies_over_stdio(tmp_path: Path):
    db_path = tmp_path / "fixture.db"
    conn = open_db(db_path)
    services_repo.ensure_service(conn, "orders-service", "/tmp/orders-service", "node-ts")
    service_id = services_repo.get_service_by_name(conn, "orders-service")["id"]
    flows_repo.replace_analysis(conn, service_id, AnalysisResult(cloud_facts=[
        CloudFact("aws", "queue", "sqs", "SendMessage", "publish", "aws-sdk-js-v3", None, Evidence("a.ts", 1, 1)),
    ]))
    conn.commit()
    conn.close()

    params = server_params(db_path)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = content_json(await session.call_tool(
                "describe_cloud_dependencies", {"service": "orders-service"},
            ))
            assert result["static_facts"][0]["service_name"] == "sqs"
