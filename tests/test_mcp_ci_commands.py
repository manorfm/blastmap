from orbitkb.db.connection import open_db
from orbitkb.db.repositories import ci_commands
from orbitkb.db.repositories import repositories as repositories_repo
from orbitkb.mcp import queries


def test_describe_ci_commands_returns_paginated_repository_commands(tmp_path):
    conn = open_db(tmp_path / "test.db")
    repository_id = repositories_repo.ensure_repository(conn, "commerce", "/tmp/commerce")
    ci_commands.replace_ci_commands(conn, repository_id, [
        {
            "workflow_path": ".github/workflows/ci.yml", "kind": "test", "command": "npm test",
            "evidence": {"file": ".github/workflows/ci.yml", "start_line": 5, "end_line": 5},
        },
        {
            "workflow_path": ".github/workflows/ci.yml", "kind": "build", "command": "npm run build",
            "evidence": {"file": ".github/workflows/ci.yml", "start_line": 6, "end_line": 6},
        },
    ])

    assert queries.describe_ci_commands(conn, "commerce", limit=1, offset=1) == {
        "repository": "commerce",
        "commands": [{
            "workflow_path": ".github/workflows/ci.yml", "kind": "build", "command": "npm run build",
            "evidence": {"file": ".github/workflows/ci.yml", "start_line": 6, "end_line": 6},
        }],
        "total": 2,
        "truncated": False,
    }
