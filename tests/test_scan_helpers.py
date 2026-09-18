from pathlib import Path

from context_insight.discovery.scan_helpers import collect_config_excerpts


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
