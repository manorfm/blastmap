from orbitkb.db.connection import open_db
from orbitkb.db.repositories import change_plans, ci_commands, ci_validation_results
from orbitkb.db.repositories import repositories as repositories_repo


def test_ci_validation_results_replace_the_current_status_without_storing_command_output(tmp_path):
    conn = open_db(tmp_path / "test.db")
    repository_id = repositories_repo.ensure_repository(conn, "commerce", "/tmp/commerce")
    plan_id = change_plans.record_plan(conn, None, "ready", 100, [], [])
    ci_commands.replace_ci_commands(conn, repository_id, [{
        "workflow_path": ".github/workflows/ci.yml", "kind": "test", "command": "pytest -q",
        "evidence": {"file": ".github/workflows/ci.yml", "start_line": 5, "end_line": 5},
    }])

    ci_validation_results.record_result(
        conn, plan_id, repository_id, {
            "workflow_path": ".github/workflows/ci.yml", "kind": "test", "command": "pytest -q",
            "start_line": 5, "status": "failed", "duration_ms": 1200,
        },
    )
    ci_validation_results.record_result(
        conn, plan_id, repository_id, {
            "workflow_path": ".github/workflows/ci.yml", "kind": "test", "command": "pytest -q",
            "start_line": 5, "status": "passed", "duration_ms": 900,
        },
    )

    assert [dict(row) for row in ci_validation_results.list_results(conn, plan_id, repository_id)] == [{
        "workflow_path": ".github/workflows/ci.yml",
        "kind": "test",
        "command": "pytest -q",
        "start_line": 5,
        "status": "passed",
        "duration_ms": 900,
    }]
