.PHONY: help install dev hooks test coverage lint sast sca security dast verify \
        build clean release release-patch release-minor release-major _release

# Defaults to the project's own .venv when one exists, so `make verify`/
# `make release` use the interpreter `make dev` installed into -- not
# whatever `python3`/`ruff`/`bandit`/etc. happen to be first on PATH (which,
# unactivated, can silently be an unrelated interpreter with none of this
# project's dev dependencies installed). Override with `make PYTHON=...`.
ifeq ($(origin PYTHON),undefined)
PYTHON := $(shell test -x .venv/bin/python3 && echo .venv/bin/python3 || echo python3)
endif

# Also put .venv/bin first on PATH for every recipe, so things spawned as a
# bare command (the `impactmesh` console script itself, in tests/test_rename_smoke.py)
# resolve to the venv's copy too, without requiring `source .venv/bin/activate`.
ifneq ($(wildcard .venv/bin),)
export PATH := $(abspath .venv/bin):$(PATH)
endif

DB_DEFAULT := $(HOME)/.impactmesh/impactmesh.db

help:
	@echo "impactmesh — available targets:"
	@echo "  install         Install the package (runtime only)"
	@echo "  dev             Install the package with dev/test/security tooling + git hooks"
	@echo "  hooks           Install git hooks (commit-msg: enforces Conventional Commits)"
	@echo "  test            Run the deterministic test suite"
	@echo "  coverage        Run tests with coverage report"
	@echo "  lint            ruff (unused imports/vars) + vulture (dead code)"
	@echo "  sast            bandit static security scan"
	@echo "  sca             pip-audit dependency vulnerability scan"
	@echo "  security        sast + sca together"
	@echo "  dast            live MCP server adversarial-input test"
	@echo "  verify          test + lint + sast + sca + dast, fanned out in parallel"
	@echo "  build           Build sdist + wheel into dist/"
	@echo "  clean           Remove build artifacts"
	@echo "  release         Auto-computed bump (from commit history), verify, commit, tag, push"
	@echo "  release-patch   Force a patch release (same pipeline as release)"
	@echo "  release-minor   Force a minor release (same pipeline as release)"
	@echo "  release-major   Force a major release (same pipeline as release)"

install:
	$(PYTHON) -m pip install .

dev:
	$(PYTHON) -m pip install -e ".[dev]"
	@$(MAKE) hooks

hooks:
	git config core.hooksPath scripts/githooks
	@echo "Git hooks installed (core.hooksPath=scripts/githooks)."

test:
	$(PYTHON) -m pytest tests/

coverage:
	$(PYTHON) -m coverage run -m pytest tests/
	$(PYTHON) -m coverage report -m

lint:
	$(PYTHON) -m ruff check --select F401,F841 impactmesh tests scripts
	$(PYTHON) -m vulture impactmesh --min-confidence 80

sast:
	$(PYTHON) -m bandit -c pyproject.toml -r impactmesh

sca:
	$(PYTHON) -m pip_audit

security: sast sca

dast:
	$(PYTHON) -m pytest tests/test_dast_adversarial_inputs.py -v

# Fan-out/fan-in release gate: test/lint/sast/sca/dast run concurrently in
# scripts/preflight.sh; a single failure aborts release with no git side
# effects. See the script for why this isn't the *authoritative* gate.
verify:
	@scripts/preflight.sh

build: clean
	$(PYTHON) -m build

clean:
	rm -rf build dist *.egg-info impactmesh.egg-info

# `release` computes the bump (major/minor/patch) from Conventional Commits
# since the last tag (scripts/versioning.py — the same logic the commit-msg
# hook previews on every commit). `release-patch/minor/major` force a kind
# instead of computing it. Both feed the same `_release` pipeline: fan-out/
# fan-in verify -> bump version -> refresh README badges -> commit -> tag ->
# push. Pushing the tag is what triggers .github/workflows/publish.yml, which
# reruns test/lint/sast/sca/dast in CI (the real gate) before build/publish.
release:
	@kind=$$($(PYTHON) scripts/versioning.py next) || exit 1; \
	$(MAKE) _release KIND=$$kind

release-patch:
	@$(MAKE) _release KIND=patch

release-minor:
	@$(MAKE) _release KIND=minor

release-major:
	@$(MAKE) _release KIND=major

_release: verify
	@branch=$$(git symbolic-ref --short HEAD); \
	if [ "$$branch" != "main" ]; then \
		echo "release: must be on main (currently on $$branch)" >&2; exit 1; \
	fi
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo "release: working tree is not clean" >&2; exit 1; \
	fi
	git fetch origin main --quiet
	@if [ -n "$$(git rev-list HEAD..origin/main)" ]; then \
		echo "release: local main is behind origin/main -- pull first" >&2; exit 1; \
	fi
	$(PYTHON) scripts/bump_version.py $(KIND)
	$(eval NEW_VERSION := $(shell $(PYTHON) -c "import impactmesh; print(impactmesh.__version__)"))
	$(PYTHON) scripts/ensure_badges.py
	git add pyproject.toml impactmesh/__init__.py README.md
	git commit -m "chore: released v$(NEW_VERSION)"
	git tag "v$(NEW_VERSION)"
	git push origin main
	git push origin "v$(NEW_VERSION)"
	@echo ""
	@echo "Pushed v$(NEW_VERSION). publish.yml will rerun test/lint/sast/sca/dast"
	@echo "and only publish to PyPI if every one of them passes."
