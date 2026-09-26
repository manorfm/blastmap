"""Regression coverage for scripts/cut_release.sh.

Found live during a real `make release` run: the old Makefile `_release` target
captured the new version with a separate recipe line,
`$(eval NEW_VERSION := $(shell python -c "import orbitkb; print(orbitkb.__version__)"))`.
On GNU Make 3.81 (what macOS ships), that `$(shell ...)` is evaluated before the
*previous* recipe line's `bump_version.py` side effect is observed, so the commit
message and the git tag were always named with the version *before* the bump, one
release early. Confirmed against real history: tag `v1.2.0`'s commit already holds
`__version__ = "1.2.1"` in the file.

`cut_release.sh` fixes this by running the whole bump -> commit -> tag -> push
sequence as one shell script (a single subprocess), so `new_version` is captured
with plain shell command substitution immediately after `bump_version.py` runs in
the same process, with no Make-level expansion in between.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CUT_RELEASE = REPO_ROOT / "scripts" / "cut_release.sh"


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_scratch_project(root: Path, version: str) -> Path:
    """A minimal project shaped like this repo: a bare 'origin' remote, a `main`
    branch, and the two files bump_version.py/ensure_badges.py actually touch."""
    origin = root / "origin.git"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)

    work = root / "work"
    work.mkdir()
    _run_git(["init", "-q", "-b", "main"], work)
    _run_git(["config", "user.email", "t@example.com"], work)
    _run_git(["config", "user.name", "t"], work)
    _run_git(["remote", "add", "origin", str(origin)], work)

    (work / "orbitkb").mkdir()
    (work / "orbitkb" / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (work / "pyproject.toml").write_text(
        '[project]\n'
        'name = "orbitkb"\n'
        f'version = "{version}"\n'
        'urls = { Repository = "https://github.com/example/orbitkb" }\n',
        encoding="utf-8",
    )
    (work / "README.md").write_text("# orbitkb\n\nScratch project.\n", encoding="utf-8")
    (work / "scripts").mkdir()
    for name in ("bump_version.py", "ensure_badges.py"):
        shutil.copy(REPO_ROOT / "scripts" / name, work / "scripts" / name)

    _run_git(["add", "."], work)
    _run_git(["commit", "-q", "-m", "chore: initial scratch project"], work)
    _run_git(["push", "-q", "-u", "origin", "main"], work)
    return work


def test_cut_release_names_the_commit_and_tag_after_the_bumped_version(tmp_path: Path):
    work = _init_scratch_project(tmp_path, "1.0.0")

    result = subprocess.run(
        ["bash", str(CUT_RELEASE), "minor"], cwd=work, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert 'version = "1.1.0"' in (work / "pyproject.toml").read_text(encoding="utf-8")
    commit_subject = _run_git(["log", "-1", "--format=%s"], work).stdout.strip()
    assert commit_subject == "chore: released v1.1.0"
    tags = _run_git(["tag", "-l"], work).stdout.split()
    assert tags == ["v1.1.0"]
    tagged_commit = _run_git(["rev-list", "-n", "1", "v1.1.0"], work).stdout.strip()
    head_commit = _run_git(["rev-parse", "HEAD"], work).stdout.strip()
    assert tagged_commit == head_commit


def test_cut_release_refuses_off_main(tmp_path: Path):
    work = _init_scratch_project(tmp_path, "1.0.0")
    _run_git(["checkout", "-q", "-b", "feature"], work)

    result = subprocess.run(
        ["bash", str(CUT_RELEASE), "minor"], cwd=work, capture_output=True, text=True, check=False,
    )

    assert result.returncode != 0
    assert "must be on main" in result.stderr
    assert 'version = "1.0.0"' in (work / "pyproject.toml").read_text(encoding="utf-8")


def test_cut_release_refuses_a_dirty_working_tree(tmp_path: Path):
    work = _init_scratch_project(tmp_path, "1.0.0")
    (work / "README.md").write_text("# orbitkb\n\nuncommitted change\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(CUT_RELEASE), "minor"], cwd=work, capture_output=True, text=True, check=False,
    )

    assert result.returncode != 0
    assert "working tree is not clean" in result.stderr
