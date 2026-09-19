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
    (tmp_path / "application.properties").write_text("X" * 10_000)

    excerpts = collect_config_excerpts(tmp_path)

    assert len(excerpts[0].text) == 4_000


def test_config_evidence_excludes_dotenv_and_redacts_secret_values(tmp_path: Path):
    (tmp_path / ".env").write_text("DATABASE_PASSWORD=do-not-send\n", encoding="utf-8")
    (tmp_path / "application.yml").write_text(
        "spring:\n  datasource:\n    password: do-not-send\n    url: jdbc:postgresql://db/orders\n",
        encoding="utf-8",
    )

    excerpts = collect_config_excerpts(tmp_path)

    assert [excerpt.file_path for excerpt in excerpts] == ["application.yml"]
    assert "do-not-send" not in excerpts[0].text
    assert "jdbc:postgresql://db/orders" in excerpts[0].text


def test_engine_hint_from_manifest_matches_a_known_driver(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("fastapi\npsycopg2-binary==2.9\n")

    engine = engine_hint_from_manifest(tmp_path, ("requirements.txt",), {"psycopg2": "postgres", "pymysql": "mysql"})

    assert engine == "postgres"


def test_engine_hint_from_manifest_returns_none_when_no_driver_found(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("fastapi\n")

    engine = engine_hint_from_manifest(tmp_path, ("requirements.txt",), {"psycopg2": "postgres"})

    assert engine is None
