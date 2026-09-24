"""Confirms find_change_surface is wired into the real MCP server over stdio.

Uses a task that matches nothing in an empty DB, which short-circuits before any
LLM backend call — so this proves the tool is registered and reachable through the
real transport without needing a real `claude`/`codex` CLI available in the test env.
The actual candidate-retrieval/synthesis/anti-hallucination logic is covered more
thoroughly (with a fake backend) in tests/test_change_surface.py.
"""
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from orbitkb.db.connection import open_db
from tests.mcp_test_helpers import content_json, server_params


def test_server_module_cli_exposes_backend_selection():
    """python -m orbitkb.mcp.server is the entry point real MCP clients (Claude
    Desktop, etc.) configure directly — it must expose --backend/--model itself and not
    only through `orbitkb serve`, or find_change_surface is silently stuck on
    whatever DEFAULT_BACKEND happens to be.
    """
    result = subprocess.run(
        [sys.executable, "-m", "orbitkb.mcp.server", "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert "--backend" in result.stdout
    assert "--model" in result.stdout


@pytest.mark.anyio
async def test_find_change_surface_is_reachable_over_stdio(tmp_path: Path):
    db_path = tmp_path / "empty.db"
    open_db(db_path).close()

    params = server_params(db_path)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = content_json(
                await session.call_tool("find_change_surface", {"task": "completely unrelated task xyz"})
            )
            assert result["primary"] == []
            assert result["secondary"] == []
            assert result["no_change_hint"] == []
            assert "note" in result


@pytest.mark.anyio
async def test_get_change_context_is_reachable_over_stdio(tmp_path: Path):
    db_path = tmp_path / "empty.db"
    open_db(db_path).close()

    params = server_params(db_path)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = content_json(await session.call_tool("get_change_context", {"task": "unrelated xyz"}))
            assert result["services"] == []
            assert "note" in result


@pytest.mark.anyio
async def test_plan_change_is_reachable_over_stdio(tmp_path: Path):
    db_path = tmp_path / "empty.db"
    open_db(db_path).close()

    params = server_params(db_path)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = content_json(await session.call_tool("plan_change", {"task": "unrelated xyz"}))

            assert result["plan_id"] is None
            assert result["status"] == "insufficient_evidence"
            assert result["surface"] == {"primary": [], "secondary": [], "contracts_at_risk": []}
            assert result["decision_points"] == []
            assert result["change_units"] == []
