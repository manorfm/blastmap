"""TDD coverage for scripts/bump_version.py's manual release-time bump interface
(no longer invoked by a git hook — see scripts/bump_version.py docstring)."""
import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "bump_version.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("bump_version", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bump_version = _load_module()


def test_next_version_minor_resets_patch():
    assert bump_version.next_version("1.2.5", "minor") == "1.3.0"


def test_next_version_major_resets_minor_and_patch():
    assert bump_version.next_version("1.2.5", "major") == "2.0.0"


def test_next_version_patch_increments_last_segment():
    assert bump_version.next_version("1.2.5", "patch") == "1.2.6"


def test_next_version_rejects_unknown_kind():
    try:
        bump_version.next_version("1.2.5", "sideways")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for an unknown bump kind")


def _write_project_files(tmp_path: Path, version: str) -> tuple[Path, Path]:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(f'[project]\nname = "orbitkb"\nversion = "{version}"\n', encoding="utf-8")
    init_file = tmp_path / "__init__.py"
    init_file.write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    return pyproject, init_file


def test_bump_updates_pyproject_and_init_together(tmp_path):
    pyproject, init_file = _write_project_files(tmp_path, "1.2.0")

    current, new = bump_version.bump(pyproject, init_file, "minor")

    assert (current, new) == ("1.2.0", "1.3.0")
    assert 'version = "1.3.0"' in pyproject.read_text(encoding="utf-8")
    assert '__version__ = "1.3.0"' in init_file.read_text(encoding="utf-8")


def test_main_rejects_missing_or_invalid_argument(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["bump_version.py"])
    assert bump_version.main() == 1
    assert "usage" in capsys.readouterr().err

    monkeypatch.setattr(sys, "argv", ["bump_version.py", "sideways"])
    assert bump_version.main() == 1


def test_main_bumps_the_real_configured_paths(tmp_path, monkeypatch, capsys):
    pyproject, init_file = _write_project_files(tmp_path, "0.1.0")
    monkeypatch.setattr(bump_version, "PYPROJECT", pyproject)
    monkeypatch.setattr(bump_version, "INIT_FILE", init_file)
    monkeypatch.setattr(sys, "argv", ["bump_version.py", "patch"])

    assert bump_version.main() == 0
    assert "0.1.0 -> 0.1.1" in capsys.readouterr().out
