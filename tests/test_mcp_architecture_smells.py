"""Integration test for find_architecture_smells: drives the real MCP server over
stdio against a deterministic fixture DB, the same pattern test_mcp_relationships.py
uses."""
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from context_insight.db.connection import open_db
from context_insight.db.repositories import apis as apis_repo
from context_insight.db.repositories import service_calls as service_calls_repo
from context_insight.db.repositories import services as services_repo
from context_insight.generation.architecture import recompute_architecture_view

from tests.mcp_test_helpers import content_json, server_params


def _build_fixture_db(db_path: Path) -> None:
    conn = open_db(db_path)
    a_id = services_repo.ensure_service(conn, "a-service", "/tmp/a", "python")
    b_id = services_repo.ensure_service(conn, "b-service", "/tmp/b", "python")
    a_api = apis_repo.upsert_api(conn, a_id, "GET", "/a", "s", "d", [], [{"file": "a.py", "start_line": 1, "end_line": 5}])
    b_api = apis_repo.upsert_api(conn, b_id, "GET", "/b", "s", "d", [], [{"file": "b.py", "start_line": 1, "end_line": 5}])
    service_calls_repo.replace_calls_for_api(
        conn, a_id, a_api,
        [{"to_service_name": "b-service", "call_kind": "http", "reason": "r", "data_needed": [],
          "purpose_kind": "other", "confidence": 0.9, "target_kind": "unknown"}],
        [],
    )
    service_calls_repo.replace_calls_for_api(
        conn, b_id, b_api,
        [{"to_service_name": "a-service", "call_kind": "http", "reason": "r", "data_needed": [],
          "purpose_kind": "other", "confidence": 0.9, "target_kind": "unknown"}],
        [],
    )
    service_calls_repo.reconcile_service_call_targets(conn)
    recompute_architecture_view(conn)
    conn.close()


@pytest.mark.anyio
async def test_find_architecture_smells_reports_a_cycle(tmp_path: Path):
    db_path = tmp_path / "fixture.db"
    _build_fixture_db(db_path)

    params = server_params(db_path)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = content_json(await session.call_tool("find_architecture_smells", {}))

            assert result["run_id"] is not None
            cycle_findings = [f for f in result["findings"] if f["kind"] == "cycle"]
            assert len(cycle_findings) == 1
            assert set(cycle_findings[0]["services"]) == {"a-service", "b-service"}


@pytest.mark.anyio
async def test_find_architecture_smells_with_no_run_yet(tmp_path: Path):
    db_path = tmp_path / "empty.db"
    open_db(db_path).close()

    params = server_params(db_path)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = content_json(await session.call_tool("find_architecture_smells", {}))

            assert result["run_id"] is None
            assert result["findings"] == []
