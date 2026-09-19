"""Integration test for the verify_change_surface MCP tool: drives the real MCP
server over stdio against a deterministic fixture DB + a real (throwaway) git repo."""
import subprocess
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from orbitkb.db.connection import open_db
from orbitkb.db.repositories import change_surface as change_surface_repo
from orbitkb.db.repositories import repositories as repositories_repo
from orbitkb.db.repositories import services as services_repo

from tests.mcp_test_helpers import content_json, server_params


def _build_fixture(root: Path, db_path: Path) -> tuple[int, str]:
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "checkout-service").mkdir()
    (root / "checkout-service" / "main.py").write_text("1")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "first"], cwd=root, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()

    conn = open_db(db_path)
    repo_id = repositories_repo.ensure_repository(conn, "checkout-repo", str(root))
    services_repo.ensure_service(conn, "checkout-service", str(root / "checkout-service"), "python", repository_id=repo_id)
    run_id = change_surface_repo.record_change_surface_run(
        conn, "task", "claude",
        {"primary": [{"service": "checkout-service", "reason": "r", "confidence": 0.9, "evidence": []}],
         "secondary": [], "no_change_hint": [], "external_integrations": [], "unmapped_internal_hint": []},
    )
    conn.close()

    (root / "checkout-service" / "main.py").write_text("2")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "second"], cwd=root, check=True)
    return run_id, commit


@pytest.mark.anyio
async def test_verify_change_surface_over_stdio(tmp_path: Path):
    db_path = tmp_path / "fixture.db"
    run_id, commit = _build_fixture(tmp_path / "repo", db_path)

    params = server_params(db_path)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = content_json(
                await session.call_tool(
                    "verify_change_surface",
                    {"run_id": run_id, "repository": "checkout-repo", "since_commit": commit},
                )
            )
            assert result["true_positives"] == ["checkout-service"]
            assert result["precision"] == 1.0
            assert result["recall"] == 1.0
