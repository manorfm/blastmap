from orbitkb.db.connection import open_db
from orbitkb.db.repositories import ci_commands
from orbitkb.db.repositories import repositories as repositories_repo


def test_ci_commands_are_replaced_per_repository_and_ordered(tmp_path):
    conn = open_db(tmp_path / "test.db")
    repository_id = repositories_repo.ensure_repository(conn, "commerce", "/tmp/commerce")

    ci_commands.replace_ci_commands(conn, repository_id, [
        {
            "workflow_path": ".github/workflows/build.yml",
            "kind": "build",
            "command": "npm run build",
            "evidence": {"file": ".github/workflows/build.yml", "start_line": 8, "end_line": 8},
        },
        {
            "workflow_path": ".github/workflows/ci.yml",
            "kind": "test",
            "command": "npm test",
            "evidence": {"file": ".github/workflows/ci.yml", "start_line": 5, "end_line": 5},
        },
    ])

    assert [dict(row) for row in ci_commands.list_ci_commands(conn, repository_id)] == [
        {
            "workflow_path": ".github/workflows/build.yml",
            "kind": "build",
            "command": "npm run build",
            "file_path": ".github/workflows/build.yml",
            "start_line": 8,
            "end_line": 8,
        },
        {
            "workflow_path": ".github/workflows/ci.yml",
            "kind": "test",
            "command": "npm test",
            "file_path": ".github/workflows/ci.yml",
            "start_line": 5,
            "end_line": 5,
        },
    ]

    ci_commands.replace_ci_commands(conn, repository_id, [])

    assert ci_commands.list_ci_commands(conn, repository_id) == []
