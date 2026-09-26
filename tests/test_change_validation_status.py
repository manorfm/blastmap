from jsonschema import validate

from orbitkb.db.connection import open_db
from orbitkb.db.repositories import change_plans, ci_commands, ci_validation_results
from orbitkb.db.repositories import repositories as repositories_repo
from orbitkb.generation.llm_harness import load_schema
from orbitkb.mcp import queries


def test_change_validation_status_summarizes_compact_current_results(tmp_path):
    conn = open_db(tmp_path / "test.db")
    repository_id = repositories_repo.ensure_repository(conn, "commerce", "/tmp/commerce")
    plan_id = change_plans.record_plan(conn, None, "ready", 100, [], [])

    assert queries.describe_change_validation_status(conn, f"cp_{plan_id}", "commerce") == {
        "plan_id": f"cp_{plan_id}",
        "repository": "commerce",
        "status": "no_indexed_commands",
        "summary": {"total": 0, "passed": 0, "failed": 0, "pending": 0},
        "commands": [],
    }

    ci_commands.replace_ci_commands(conn, repository_id, [
        {
            "workflow_path": ".github/workflows/ci.yml", "kind": "test", "command": "pytest -q",
            "evidence": {"file": ".github/workflows/ci.yml", "start_line": 5, "end_line": 5},
        },
        {
            "workflow_path": ".github/workflows/ci.yml", "kind": "build", "command": "npm run build",
            "evidence": {"file": ".github/workflows/ci.yml", "start_line": 6, "end_line": 6},
        },
        {
            "workflow_path": ".github/workflows/ci.yml", "kind": "test", "command": "pytest integration",
            "evidence": {"file": ".github/workflows/ci.yml", "start_line": 7, "end_line": 7},
        },
    ])
    ci_validation_results.record_result(conn, plan_id, repository_id, {
        "workflow_path": ".github/workflows/ci.yml", "kind": "test", "command": "pytest -q",
        "start_line": 5, "status": "passed", "duration_ms": 800,
    })
    ci_validation_results.record_result(conn, plan_id, repository_id, {
        "workflow_path": ".github/workflows/ci.yml", "kind": "build", "command": "npm run build",
        "start_line": 6, "status": "failed", "duration_ms": None,
    })

    result = queries.describe_change_validation_status(conn, f"cp_{plan_id}", "commerce")

    assert result == {
        "plan_id": f"cp_{plan_id}",
        "repository": "commerce",
        "status": "failed",
        "summary": {"total": 3, "passed": 1, "failed": 1, "pending": 1},
        "commands": [
            {
                "kind": "test", "command": "pytest -q", "workflow_path": ".github/workflows/ci.yml",
                "status": "passed", "duration_ms": 800,
            },
            {
                "kind": "build", "command": "npm run build", "workflow_path": ".github/workflows/ci.yml",
                "status": "failed", "duration_ms": None,
            },
            {
                "kind": "test", "command": "pytest integration", "workflow_path": ".github/workflows/ci.yml",
                "status": "pending", "duration_ms": None,
            },
        ],
    }
    validate(result, load_schema("change_validation_status"))

    ci_validation_results.record_result(conn, plan_id, repository_id, {
        "workflow_path": ".github/workflows/ci.yml", "kind": "build", "command": "npm run build",
        "start_line": 6, "status": "passed", "duration_ms": 1100,
    })
    ci_validation_results.record_result(conn, plan_id, repository_id, {
        "workflow_path": ".github/workflows/ci.yml", "kind": "test", "command": "pytest integration",
        "start_line": 7, "status": "passed", "duration_ms": 1500,
    })

    assert queries.describe_change_validation_status(conn, f"cp_{plan_id}", "commerce")["status"] == "reported_passed"
