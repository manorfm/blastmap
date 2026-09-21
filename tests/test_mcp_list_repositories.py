"""Coverage for the list_repositories MCP tool: an agent working cumulatively across
several indexed repositories needs a way to see what's already indexed without
shelling out to the CLI (see README's "cumulative knowledge" framing)."""
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from orbitkb.db.connection import open_db
from orbitkb.db.repositories import repositories as repositories_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.mcp import queries

from tests.mcp_test_helpers import content_json, server_params


def test_list_repositories_reports_name_root_path_and_service_count(tmp_path: Path):
    conn = open_db(tmp_path / "fixture.db")
    repo_id = repositories_repo.ensure_repository(conn, "mono-repo", "/tmp/mono-repo")
    repositories_repo.ensure_repository(conn, "empty-repo", "/tmp/empty-repo")
    services_repo.ensure_service(conn, "checkout-service", "/tmp/mono-repo/checkout", "python", repository_id=repo_id)

    result = queries.list_repositories(conn)

    by_name = {r["name"]: r for r in result["repositories"]}
    assert by_name["mono-repo"] == {"name": "mono-repo", "root_path": "/tmp/mono-repo", "service_count": 1}
    assert by_name["empty-repo"]["service_count"] == 0


@pytest.mark.anyio
async def test_list_repositories_over_stdio(tmp_path: Path):
    db_path = tmp_path / "fixture2.db"
    conn = open_db(db_path)
    repositories_repo.ensure_repository(conn, "mono-repo", "/tmp/mono-repo")
    conn.close()

    params = server_params(db_path)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = content_json(await session.call_tool("list_repositories", {}))
            assert result["repositories"][0]["name"] == "mono-repo"


def test_mcp_requires_repository_to_disambiguate_same_named_services(tmp_path: Path):
    conn = open_db(tmp_path / "fixture3.db")
    checkout_id = repositories_repo.ensure_repository(conn, "checkout-repo", "/tmp/checkout-repo")
    fulfillment_id = repositories_repo.ensure_repository(conn, "fulfillment-repo", "/tmp/fulfillment-repo")
    services_repo.ensure_service(conn, "orders", "/tmp/checkout-repo/orders", "go", repository_id=checkout_id)
    services_repo.ensure_service(conn, "orders", "/tmp/fulfillment-repo/orders", "jvm-spring", repository_id=fulfillment_id)

    listing = queries.list_services(conn)
    ambiguous = queries.describe_service(conn, "orders")
    selected = queries.describe_service(conn, "orders", repository="fulfillment-repo")

    assert {(item["name"], item["repository"]) for item in listing["services"]} == {
        ("orders", "checkout-repo"), ("orders", "fulfillment-repo"),
    }
    assert ambiguous == {
        "error": "ambiguous service: orders; specify repository",
        "repositories": ["checkout-repo", "fulfillment-repo"],
    }
    assert selected["repository"] == "fulfillment-repo"
    assert selected["stack"] == "jvm-spring"
