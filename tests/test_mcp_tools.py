"""Integration test: drives the real MCP server over stdio, exactly as an agent would."""
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from orbitkb.db.connection import open_db
from orbitkb.generation.orchestrator import index_path
from tests.mcp_test_helpers import content_json as _content_json
from tests.mcp_test_helpers import server_params
from tests.test_orchestrator import SAMPLE_ROOT, FakeOrchestratorBackend


@pytest.mark.anyio
async def test_mcp_progressive_disclosure_flow(tmp_path: Path):
    """Indexes the fixture first, never relying on a developer's local database."""
    db_path = tmp_path / "sample-project.db"
    conn = open_db(db_path)
    index_path(conn, SAMPLE_ROOT, FakeOrchestratorBackend())
    conn.close()

    params = server_params(db_path)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            assert "validate_runtime_configuration_follow_up" in {tool.name for tool in tools.tools}

            services = _content_json(await session.call_tool("list_services", {}))
            names = {s["name"] for s in services["services"]}
            assert {"orders-service", "payments-service", "inventory-service"} <= names

            service = _content_json(await session.call_tool("describe_service", {"service": "orders-service"}))
            assert service["name"] == "orders-service"
            call_targets = {c["to_service_name"] for c in service["calls"]}
            assert "payments-service" in call_targets

            apis = _content_json(await session.call_tool("list_apis", {"service": "orders-service"}))
            assert any(a["method"] == "POST" and a["path"] == "/orders" for a in apis["apis"])

            detail = _content_json(
                await session.call_tool("describe_api", {"service": "orders-service", "method": "POST", "path": "/orders"})
            )
            detail_targets = {c["to_service_name"] for c in detail["calls"]}
            assert "payments-service" in detail_targets
            assert any(v["kind"] == "authorization" for v in detail["validations"])

            search_result = _content_json(await session.call_tool("search", {"query": "payment"}))
            assert len(search_result["results"]) > 0
