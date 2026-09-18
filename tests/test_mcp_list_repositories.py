"""Coverage for the list_repositories MCP tool: an agent working cumulatively across
several indexed repositories needs a way to see what's already indexed without
shelling out to the CLI (see README's "cumulative knowledge" framing)."""
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from context_insight.db.connection import open_db
from context_insight.db.repositories import repositories as repositories_repo
from context_insight.db.repositories import services as services_repo
from context_insight.mcp import queries

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
