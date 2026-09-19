.PHONY: help install dev test coverage lint sast sca security build clean \
        release-patch release-minor release-major _release

PYTHON ?= python3
DB_DEFAULT := $(HOME)/.orbitkb/orbitkb.db

help:
	@echo "orbitkb — available targets:"
	@echo "  install         Install the package (runtime only)"
	@echo "  dev             Install the package with dev/test/security tooling"
	@echo "  test            Run the deterministic test suite"
	@echo "  coverage        Run tests with coverage report"
	@echo "  lint            ruff (unused imports/vars) + vulture (dead code)"
	@echo "  sast            bandit static security scan"
	@echo "  sca             pip-audit dependency vulnerability scan"
	@echo "  security        sast + sca together"
	@echo "  build           Build sdist + wheel into dist/"
	@echo "  clean           Remove build artifacts"
	@echo "  release-patch   Bump patch version, commit, tag (does not push)"
	@echo "  release-minor   Bump minor version, commit, tag (does not push)"
	@echo "  release-major   Bump major version, commit, tag (does not push)"

install:
	$(PYTHON) -m pip install .

dev:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest tests/

coverage:
	$(PYTHON) -m coverage run -m pytest tests/
	$(PYTHON) -m coverage report -m

lint:
	ruff check --select F401,F841 orbitkb tests scripts
	vulture orbitkb --min-confidence 80

sast:
	bandit -c pyproject.toml -r orbitkb

sca:
	pip-audit

security: sast sca

build: clean
	$(PYTHON) -m build

clean:
	rm -rf build dist *.egg-info orbitkb.egg-info

# Bumps the version, commits pyproject.toml + orbitkb/__init__.py, and tags the
# commit as vX.Y.Z — deliberately does NOT push. Review the diff and push
# yourself (`git push && git push --tags`), which is also what triggers
# .github/workflows/publish.yml to build and publish to PyPI.
release-patch:
	@$(MAKE) _release KIND=patch

release-minor:
	@$(MAKE) _release KIND=minor

release-major:
	@$(MAKE) _release KIND=major

_release:
	$(PYTHON) scripts/bump_version.py $(KIND)
	$(eval NEW_VERSION := $(shell $(PYTHON) -c "import orbitkb; print(orbitkb.__version__)"))
	git add pyproject.toml orbitkb/__init__.py
	git commit -m "chore: released v$(NEW_VERSION)"
	git tag "v$(NEW_VERSION)"
	@echo ""
	@echo "Tagged v$(NEW_VERSION) locally. Review it, then push with:"
	@echo "  git push && git push origin v$(NEW_VERSION)"
