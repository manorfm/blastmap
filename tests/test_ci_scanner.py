from orbitkb.ci.scanner import scan_github_actions_commands


def test_scan_github_actions_commands_keeps_literal_safe_validation_commands(tmp_path):
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        """name: CI
jobs:
  verify:
    steps:
      - run: npm test
      - run: ./gradlew build
      - run: npx prisma migrate deploy
      - run: buf generate
      - run: echo ${{ secrets.DEPLOY_TOKEN }}
      - run: |
          npm run integration
""",
        encoding="utf-8",
    )

    assert scan_github_actions_commands(tmp_path) == [
        {
            "workflow_path": ".github/workflows/ci.yml",
            "kind": "test",
            "command": "npm test",
            "evidence": {"file": ".github/workflows/ci.yml", "start_line": 5, "end_line": 5},
        },
        {
            "workflow_path": ".github/workflows/ci.yml",
            "kind": "build",
            "command": "./gradlew build",
            "evidence": {"file": ".github/workflows/ci.yml", "start_line": 6, "end_line": 6},
        },
        {
            "workflow_path": ".github/workflows/ci.yml",
            "kind": "migration",
            "command": "npx prisma migrate deploy",
            "evidence": {"file": ".github/workflows/ci.yml", "start_line": 7, "end_line": 7},
        },
        {
            "workflow_path": ".github/workflows/ci.yml",
            "kind": "client_generation",
            "command": "buf generate",
            "evidence": {"file": ".github/workflows/ci.yml", "start_line": 8, "end_line": 8},
        },
    ]
