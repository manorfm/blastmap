"""TDD coverage for freshness wired into describe_service and find_change_surface
(see generation/freshness.py for the pure derivation logic itself)."""
import subprocess
from pathlib import Path

from context_insight.db.connection import open_db
from context_insight.db.repositories import search as search_repo
from context_insight.db.repositories import services as services_repo
from context_insight.generation import change_surface
from context_insight.mcp import queries

from tests.test_change_surface import FakeBackend, _build_pix_fixture


def _init_git_repo(root: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "a.txt").write_text("1")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "first"], cwd=root, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_describe_service_reports_fresh_when_commit_matches(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    commit = _init_git_repo(tmp_path)
    service_id = services_repo.ensure_service(conn, "checkout-service", str(tmp_path), "python")
    services_repo.set_service_last_commit(conn, service_id, commit)

    result = queries.describe_service(conn, "checkout-service")

    assert result["freshness"]["stale"] is False
    assert result["freshness"]["source_commit"] == commit


def test_describe_service_reports_stale_after_a_new_commit(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    commit = _init_git_repo(tmp_path)
    service_id = services_repo.ensure_service(conn, "checkout-service", str(tmp_path), "python")
    services_repo.set_service_last_commit(conn, service_id, commit)

    (tmp_path / "b.txt").write_text("2")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "second"], cwd=tmp_path, check=True)

    result = queries.describe_service(conn, "checkout-service")

    assert result["freshness"]["stale"] is True


def test_find_change_surface_reports_freshness_per_relevant_service(tmp_path: Path):
    conn = _build_pix_fixture(tmp_path / "pix.db")
    backend = FakeBackend({
        "primary": [{"service": "checkout-service", "reason": "owns checkout entry point", "confidence": 0.95}],
        "secondary": [], "no_change": [],
    })

    result = change_surface.analyze_change_surface(conn, "Add support for Pix in checkout", backend)

    assert "checkout-service" in result["freshness"]
    assert "stale" in result["freshness"]["checkout-service"]


def test_stale_relevant_service_produces_an_unknowns_entry(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    commit = _init_git_repo(tmp_path)
    service_id = services_repo.ensure_service(conn, "checkout-service", str(tmp_path), "python")
    services_repo.set_service_last_commit(conn, service_id, commit)
    services_repo.update_service_overview(conn, service_id, "Owns checkout.", "Long.")
    search_repo.rebuild_search_index(conn)

    (tmp_path / "b.txt").write_text("2")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "second"], cwd=tmp_path, check=True)

    backend = FakeBackend({
        "primary": [{"service": "checkout-service", "reason": "owns checkout", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    })

    result = change_surface.analyze_change_surface(conn, "checkout task", backend, hint_services=["checkout-service"])

    assert result["freshness"]["checkout-service"]["stale"] is True
    stale_unknowns = [u for u in result["unknowns"] if u.get("service") == "checkout-service"]
    assert len(stale_unknowns) == 1
    assert "stale" in stale_unknowns[0]["reason"] or "may be" in stale_unknowns[0]["reason"]


def test_fresh_relevant_service_produces_no_stale_unknowns_entry(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    commit = _init_git_repo(tmp_path)
    service_id = services_repo.ensure_service(conn, "checkout-service", str(tmp_path), "python")
    services_repo.set_service_last_commit(conn, service_id, commit)
    services_repo.update_service_overview(conn, service_id, "Owns checkout.", "Long.")
    search_repo.rebuild_search_index(conn)

    backend = FakeBackend({
        "primary": [{"service": "checkout-service", "reason": "owns checkout", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    })

    result = change_surface.analyze_change_surface(conn, "checkout task", backend, hint_services=["checkout-service"])

    assert result["freshness"]["checkout-service"]["stale"] is False
    assert result["unknowns"] == []
