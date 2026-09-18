"""Exercises the CLI commands directly (not via subprocess), faking only backend
resolution so `index`/`update` never shell out to a real `claude`/`codex` CLI.
"""
from pathlib import Path

import pytest

from context_insight import cli
from context_insight.db.connection import open_db
from context_insight.db.repositories import repositories as repositories_repo
from context_insight.db.repositories import services as services_repo
from tests.test_orchestrator import SAMPLE_ROOT, FakeOrchestratorBackend


@pytest.fixture(autouse=True)
def _fake_backend(monkeypatch):
    monkeypatch.setattr(cli, "resolve_backend", lambda *a, **kw: FakeOrchestratorBackend())


def _parse(argv: list[str]):
    return cli.build_parser().parse_args(argv)


def test_index_command_indexes_sample_project(tmp_path: Path, capsys):
    db_path = tmp_path / "test.db"
    args = _parse(["index", str(SAMPLE_ROOT), "--db", str(db_path)])

    exit_code = cli._cmd_index(args)

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "orders-service" in out
    assert "status=ok" in out
    conn = open_db(db_path)
    assert len(services_repo.list_services(conn)) == 3


def test_index_command_accepts_repository_name(tmp_path: Path):
    db_path = tmp_path / "test.db"
    args = _parse(["index", str(SAMPLE_ROOT), "--db", str(db_path), "--repository-name", "custom-repo"])

    cli._cmd_index(args)

    conn = open_db(db_path)
    repos = repositories_repo.list_repositories(conn)
    assert repos[0]["name"] == "custom-repo"


def test_index_command_reports_error_on_empty_directory(tmp_path: Path, capsys):
    db_path = tmp_path / "test.db"
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    args = _parse(["index", str(empty_dir), "--db", str(db_path)])

    exit_code = cli._cmd_index(args)

    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_update_command_reindexes_known_service(tmp_path: Path, capsys):
    db_path = tmp_path / "test.db"
    cli._cmd_index(_parse(["index", str(SAMPLE_ROOT), "--db", str(db_path)]))

    exit_code = cli._cmd_update(_parse(["update", "orders-service", "--db", str(db_path)]))

    assert exit_code == 0
    assert "orders-service" in capsys.readouterr().out


def test_update_command_errors_on_unknown_service(tmp_path: Path, capsys):
    db_path = tmp_path / "test.db"
    open_db(db_path)

    exit_code = cli._cmd_update(_parse(["update", "does-not-exist", "--db", str(db_path)]))

    assert exit_code == 1
    assert "unknown service" in capsys.readouterr().err


def test_list_command_prints_indexed_services(tmp_path: Path, capsys):
    db_path = tmp_path / "test.db"
    cli._cmd_index(_parse(["index", str(SAMPLE_ROOT), "--db", str(db_path)]))

    exit_code = cli._cmd_list(_parse(["list", "--db", str(db_path)]))

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "orders-service" in out
    assert "payments-service" in out


def test_list_command_handles_empty_database(tmp_path: Path, capsys):
    db_path = tmp_path / "test.db"
    open_db(db_path)

    exit_code = cli._cmd_list(_parse(["list", "--db", str(db_path)]))

    assert exit_code == 0
    assert "nenhum serviço" in capsys.readouterr().out


def test_status_command_for_one_service(tmp_path: Path, capsys):
    db_path = tmp_path / "test.db"
    cli._cmd_index(_parse(["index", str(SAMPLE_ROOT), "--db", str(db_path)]))

    exit_code = cli._cmd_status(_parse(["status", "orders-service", "--db", str(db_path)]))

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "orders-service" in out
    assert "run#" in out


def test_status_command_global(tmp_path: Path, capsys):
    db_path = tmp_path / "test.db"
    cli._cmd_index(_parse(["index", str(SAMPLE_ROOT), "--db", str(db_path)]))

    exit_code = cli._cmd_status(_parse(["status", "--db", str(db_path)]))

    assert exit_code == 0
    assert "services indexed: 3" in capsys.readouterr().out


def test_status_command_global_shows_recent_verifications(tmp_path: Path, capsys):
    from context_insight.db.repositories import change_surface as change_surface_repo
    from context_insight.db.repositories import verification as verification_repo

    db_path = tmp_path / "test.db"
    conn = open_db(db_path)
    run_id = change_surface_repo.record_change_surface_run(
        conn, "task", "claude",
        {"primary": [], "secondary": [], "no_change_hint": [], "external_integrations": [], "unmapped_internal_hint": []},
    )
    verification_repo.record_verification(
        conn, run_id, repository="checkout-repo", since_commit="abc123",
        precision=0.5, recall=1.0, true_positives=["a"], false_positives=["b"], false_negatives=[],
    )

    exit_code = cli._cmd_status(_parse(["status", "--db", str(db_path)]))

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "checkout-repo" in out
    assert "precision=0.5" in out


def test_export_command_writes_markdown(tmp_path: Path, capsys):
    db_path = tmp_path / "test.db"
    cli._cmd_index(_parse(["index", str(SAMPLE_ROOT), "--db", str(db_path)]))
    out_dir = tmp_path / "docs"

    exit_code = cli._cmd_export(_parse(["export", "md", "--out", str(out_dir), "--db", str(db_path)]))

    assert exit_code == 0
    assert (out_dir / "orders-service" / "index.md").exists()
    assert "wrote" in capsys.readouterr().out


def test_export_command_writes_mermaid_diagrams(tmp_path: Path, capsys):
    db_path = tmp_path / "test.db"
    cli._cmd_index(_parse(["index", str(SAMPLE_ROOT), "--db", str(db_path)]))
    out_dir = tmp_path / "docs"

    exit_code = cli._cmd_export(_parse(["export", "mermaid", "--out", str(out_dir), "--db", str(db_path)]))

    assert exit_code == 0
    assert (out_dir / "topology.mmd").exists()
    assert "graph TD" in (out_dir / "topology.mmd").read_text()
    assert "wrote" in capsys.readouterr().out


def test_help_leads_with_the_index_ask_verify_mental_model(capsys):
    try:
        cli.main(["--help"])
    except SystemExit:
        pass
    out = capsys.readouterr().out
    assert "index" in out and "ask" in out.lower() and "verify" in out.lower()


def test_index_help_includes_a_runnable_example(capsys):
    try:
        cli.main(["index", "--help"])
    except SystemExit:
        pass
    out = capsys.readouterr().out
    assert "context-insight index" in out


def test_version_flag_prints_the_installed_version(capsys):
    import context_insight

    try:
        cli.main(["--version"])
    except SystemExit:
        pass
    out = capsys.readouterr().out
    assert context_insight.__version__ in out


def test_analyze_command_prints_change_surface_json(tmp_path: Path, capsys, monkeypatch):
    import json

    from tests.test_change_surface import FakeBackend

    db_path = tmp_path / "test.db"
    conn = open_db(db_path)
    service_id = services_repo.ensure_service(conn, "checkout-service", "/tmp/checkout", "python")
    services_repo.update_service_overview(conn, service_id, "Owns the checkout entry point.", "L")
    from context_insight.db.repositories import search as search_repo

    search_repo.rebuild_search_index(conn)

    fake = FakeBackend({
        "primary": [{"service": "checkout-service", "reason": "owns checkout", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    })
    monkeypatch.setattr(cli, "resolve_backend", lambda *a, **kw: fake)

    exit_code = cli.main(["analyze", "checkout task", "--db", str(db_path)])

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["primary"][0]["service"] == "checkout-service"


def test_analyze_command_accepts_hint_services(tmp_path: Path, capsys, monkeypatch):
    import json

    from tests.test_change_surface import FakeBackend

    db_path = tmp_path / "test.db"
    conn = open_db(db_path)
    services_repo.ensure_service(conn, "notification-service", "/tmp/notif", "python")

    fake = FakeBackend({
        "primary": [{"service": "notification-service", "reason": "explicitly hinted", "confidence": 0.6}],
        "secondary": [], "no_change": [],
    })
    monkeypatch.setattr(cli, "resolve_backend", lambda *a, **kw: fake)

    exit_code = cli.main([
        "analyze", "xyz unrelated", "--hint-services", "notification-service", "--db", str(db_path),
    ])

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["primary"][0]["service"] == "notification-service"
    assert fake.calls == 1


def test_main_dispatches_to_list_command(tmp_path: Path, capsys):
    db_path = tmp_path / "test.db"
    open_db(db_path)

    exit_code = cli.main(["list", "--db", str(db_path)])

    assert exit_code == 0


def test_verify_command_reports_precision_and_recall(tmp_path: Path, capsys):
    import subprocess

    from context_insight.db.repositories import change_surface as change_surface_repo
    from context_insight.db.repositories import repositories as repositories_repo

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo_root, check=True)
    (repo_root / "checkout-service").mkdir()
    (repo_root / "checkout-service" / "main.py").write_text("1")
    subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "first"], cwd=repo_root, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True
    ).stdout.strip()

    db_path = tmp_path / "test.db"
    conn = open_db(db_path)
    repo_id = repositories_repo.ensure_repository(conn, "checkout-repo", str(repo_root))
    services_repo.ensure_service(conn, "checkout-service", str(repo_root / "checkout-service"), "python", repository_id=repo_id)
    run_id = change_surface_repo.record_change_surface_run(
        conn, "task", "claude",
        {"primary": [{"service": "checkout-service", "reason": "r", "confidence": 0.9, "evidence": []}],
         "secondary": [], "no_change_hint": [], "external_integrations": [], "unmapped_internal_hint": []},
    )

    exit_code = cli.main([
        "verify", str(run_id), "--repository", "checkout-repo", "--since", commit, "--db", str(db_path),
    ])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "precision" in out
    assert "recall" in out
