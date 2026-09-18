"""TDD coverage for generation.verification: compares a past find_change_surface
prediction against what actually changed in git since a given commit, and can
auto-record feedback for the services the run actually mentioned.
"""
import subprocess
from pathlib import Path

from context_insight.db.connection import open_db
from context_insight.db.repositories import change_surface as change_surface_repo
from context_insight.db.repositories import repositories as repositories_repo
from context_insight.db.repositories import services as services_repo
from context_insight.generation import verification

SAMPLE_RESULT = {
    "primary": [
        {"service": "checkout-service", "reason": "r", "confidence": 0.9, "evidence": []},
        {"service": "payments-service", "reason": "r", "confidence": 0.8, "evidence": []},
    ],
    "secondary": [], "no_change_hint": [], "external_integrations": [], "unmapped_internal_hint": [],
}


def _init_repo_with_services(root: Path, conn) -> tuple[str, int]:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "checkout-service").mkdir()
    (root / "payments-service").mkdir()
    (root / "orders-service").mkdir()
    (root / "checkout-service" / "main.py").write_text("1")
    (root / "payments-service" / "main.py").write_text("1")
    (root / "orders-service" / "main.py").write_text("1")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "first"], cwd=root, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()

    repo_id = repositories_repo.ensure_repository(conn, "mono-repo", str(root))
    services_repo.ensure_service(conn, "checkout-service", str(root / "checkout-service"), "python", repository_id=repo_id)
    services_repo.ensure_service(conn, "payments-service", str(root / "payments-service"), "node-ts", repository_id=repo_id)
    services_repo.ensure_service(conn, "orders-service", str(root / "orders-service"), "python", repository_id=repo_id)
    return commit, repo_id


def test_verify_reports_true_positive_and_false_negative(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    commit = _init_repo_with_services(tmp_path, conn)[0]
    run_id = change_surface_repo.record_change_surface_run(conn, "task", "claude", SAMPLE_RESULT)

    # Only checkout-service actually changed; payments-service (predicted) and
    # orders-service (never mentioned) did not.
    (tmp_path / "checkout-service" / "main.py").write_text("2")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "real change"], cwd=tmp_path, check=True)

    result = verification.verify_change_surface(conn, run_id, "mono-repo", commit)

    assert result["true_positives"] == ["checkout-service"]
    assert result["false_positives"] == ["payments-service"]
    assert result["false_negatives"] == []  # orders-service never changed either
    assert result["precision"] == 0.5
    assert result["recall"] == 1.0


def test_verify_reports_false_negative_for_an_unpredicted_change(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    commit = _init_repo_with_services(tmp_path, conn)[0]
    run_id = change_surface_repo.record_change_surface_run(conn, "task", "claude", SAMPLE_RESULT)

    # orders-service changed but was never predicted at all.
    (tmp_path / "orders-service" / "main.py").write_text("2")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "real change"], cwd=tmp_path, check=True)

    result = verification.verify_change_surface(conn, run_id, "mono-repo", commit)

    assert result["false_negatives"] == ["orders-service"]


def test_verify_persists_and_can_auto_record_feedback(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    commit = _init_repo_with_services(tmp_path, conn)[0]
    run_id = change_surface_repo.record_change_surface_run(conn, "task", "claude", SAMPLE_RESULT)
    (tmp_path / "checkout-service" / "main.py").write_text("2")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "real change"], cwd=tmp_path, check=True)

    result = verification.verify_change_surface(conn, run_id, "mono-repo", commit, record_feedback=True)

    assert "verification_id" in result
    stats = change_surface_repo.get_feedback_stats(conn, "checkout-service")
    assert stats["confirmed"] == 1
    stats = change_surface_repo.get_feedback_stats(conn, "payments-service")
    assert stats["rejected"] == 1


def test_verify_errors_on_unknown_run_or_repository(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")

    assert "error" in verification.verify_change_surface(conn, 99999, "mono-repo", "HEAD")

    run_id = change_surface_repo.record_change_surface_run(conn, "task", "claude", SAMPLE_RESULT)
    assert "error" in verification.verify_change_surface(conn, run_id, "does-not-exist", "HEAD")
