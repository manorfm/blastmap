"""Integration coverage for the advisory Git assessment MCP tool."""
import subprocess
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from orbitkb.db.connection import open_db
from orbitkb.db.repositories import change_plans
from orbitkb.db.repositories import repositories as repositories_repo
from orbitkb.db.repositories import services as services_repo
from tests.mcp_test_helpers import content_json, server_params


def _build_fixture(root: Path, db_path: Path) -> tuple[str, str]:
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    service_root = root / "checkout-service"
    service_root.mkdir()
    (service_root / "client.py").write_text("before\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)
    since_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True,
    ).stdout.strip()

    conn = open_db(db_path)
    repository_id = repositories_repo.ensure_repository(conn, "commerce", str(root))
    services_repo.ensure_service(conn, "checkout-service", str(service_root), "python", repository_id=repository_id)
    plan_id = change_plans.record_plan(conn, None, "ready", 100, [], [{
        "id": "unit-1",
        "service": "checkout-service",
        "target": {"role": "integration", "symbol": "CheckoutClient.authorize", "evidence": []},
        "action": "review",
        "reason": "review the boundary",
        "preconditions": [],
        "related_contracts": ["POST /authorizations"],
        "dependencies": [],
        "validation": ["verify the client contract"],
        "confidence": 1.0,
        "evidence": [{"file": "client.py", "start_line": 1, "end_line": 1}],
    }])
    conn.close()

    (service_root / "client.py").write_text("after\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "change client"], cwd=root, check=True)
    return f"cp_{plan_id}", since_commit


@pytest.mark.anyio
async def test_assess_working_change_over_stdio(tmp_path: Path):
    db_path = tmp_path / "fixture.db"
    plan_id, since_commit = _build_fixture(tmp_path / "repo", db_path)

    async with stdio_client(server_params(db_path)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = content_json(await session.call_tool("assess_working_change", {
                "plan_id": plan_id, "repository": "commerce", "since_commit": since_commit,
            }))

    assert result["covered_change_units"] == [{
        "id": "unit-1", "changed_files": ["checkout-service/client.py"],
    }]
    assert result["omitted_change_units"] == []
