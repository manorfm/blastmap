#!/usr/bin/env bash
# Cuts a release: bumps the version, commits, tags and pushes -- all as one shell
# process. `make release`'s old `_release` recipe captured the new version with a
# separate Make recipe line, `$(eval NEW_VERSION := $(shell python -c "import
# orbitkb; print(orbitkb.__version__)"))`. On GNU Make 3.81 (what macOS ships), that
# `$(shell ...)` is evaluated before the *previous* recipe line's bump_version.py
# side effect is observed, so every past release's commit message and git tag were
# silently named one version early (confirmed against real history: tag v1.2.0's
# commit already holds `__version__ = "1.2.1"` in the file). Reading `new_version`
# with plain shell command substitution in this single script, right after
# bump_version.py runs in the same process, has no such gap.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

kind="${1:?usage: cut_release.sh <major|minor|patch>}"
python_bin="$(test -x .venv/bin/python3 && echo .venv/bin/python3 || echo python3)"

branch="$(git symbolic-ref --short HEAD)"
if [ "$branch" != "main" ]; then
  echo "release: must be on main (currently on $branch)" >&2
  exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "release: working tree is not clean" >&2
  exit 1
fi
git fetch origin main --quiet
if [ -n "$(git rev-list HEAD..origin/main)" ]; then
  echo "release: local main is behind origin/main -- pull first" >&2
  exit 1
fi

new_version="$("$python_bin" scripts/bump_version.py "$kind")"
"$python_bin" scripts/ensure_badges.py
git add pyproject.toml orbitkb/__init__.py README.md
git commit -q -m "chore: released v$new_version"
git tag "v$new_version"
git push -q origin main
git push -q origin "v$new_version"

echo ""
echo "Pushed v$new_version. publish.yml will rerun test/lint/sast/sca/dast"
echo "and only publish to PyPI if every one of them passes."
