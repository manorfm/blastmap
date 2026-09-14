"""Integration test: drives the real MCP server over stdio, exactly as an agent would."""
import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

DB_PATH = Path(__file__).resolve().parent.parent / "verify" / "sample_project.db"


def _content_json(result) -> dict:
    return json.loads(result.content[0].text)


@pytest.mark.anyio
async def test_mcp_progressive_disclosure_flow():
    if not DB_PATH.exists():
        pytest.skip(f"no indexed sample db at {DB_PATH}; run the e2e indexing step first")

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "context_insight.mcp.server", "--db", str(DB_PATH)],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

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
