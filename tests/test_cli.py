"""Exercises the CLI commands directly (not via subprocess), faking only backend
resolution so `index`/`update` never shell out to a real `claude`/`codex` CLI.
"""
from pathlib import Path

import pytest

from blastmap import cli
from blastmap.db import repository
from blastmap.db.connection import open_db
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
    assert len(repository.list_services(conn)) == 3


def test_index_command_accepts_repository_name(tmp_path: Path):
    db_path = tmp_path / "test.db"
    args = _parse(["index", str(SAMPLE_ROOT), "--db", str(db_path), "--repository-name", "custom-repo"])

    cli._cmd_index(args)

    conn = open_db(db_path)
    repos = repository.list_repositories(conn)
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


def test_export_command_writes_markdown(tmp_path: Path, capsys):
    db_path = tmp_path / "test.db"
    cli._cmd_index(_parse(["index", str(SAMPLE_ROOT), "--db", str(db_path)]))
    out_dir = tmp_path / "docs"

    exit_code = cli._cmd_export(_parse(["export", "md", "--out", str(out_dir), "--db", str(db_path)]))

    assert exit_code == 0
    assert (out_dir / "orders-service" / "index.md").exists()
    assert "wrote" in capsys.readouterr().out


def test_main_dispatches_to_list_command(tmp_path: Path, capsys):
    db_path = tmp_path / "test.db"
    open_db(db_path)

    exit_code = cli.main(["list", "--db", str(db_path)])

    assert exit_code == 0
