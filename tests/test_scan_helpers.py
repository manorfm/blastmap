from pathlib import Path

from impactmesh.discovery.scan_helpers import collect_config_excerpts, engine_hint_from_manifest


def test_collect_config_excerpts_finds_known_config_files(tmp_path: Path):
    (tmp_path / "application.yml").write_text("spring:\n  rabbitmq:\n    host: broker\n")
    (tmp_path / "unrelated.txt").write_text("not config")

    excerpts = collect_config_excerpts(tmp_path)

    assert len(excerpts) == 1
    assert excerpts[0].file_path == "application.yml"
    assert "rabbitmq" in excerpts[0].text


def test_collect_config_excerpts_returns_empty_when_none_present(tmp_path: Path):
    assert collect_config_excerpts(tmp_path) == []


def test_collect_config_excerpts_truncates_large_files(tmp_path: Path):
    (tmp_path / ".env").write_text("X" * 10_000)

    excerpts = collect_config_excerpts(tmp_path)

    assert len(excerpts[0].text) == 4_000


def test_engine_hint_from_manifest_matches_a_known_driver(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("fastapi\npsycopg2-binary==2.9\n")

    engine = engine_hint_from_manifest(tmp_path, ("requirements.txt",), {"psycopg2": "postgres", "pymysql": "mysql"})

    assert engine == "postgres"


def test_engine_hint_from_manifest_returns_none_when_no_driver_found(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("fastapi\n")

    engine = engine_hint_from_manifest(tmp_path, ("requirements.txt",), {"psycopg2": "postgres"})

    assert engine is None
