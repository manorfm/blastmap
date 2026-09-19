from pathlib import Path

from orbitkb.db.connection import open_db
from orbitkb.db.repositories import change_surface as change_surface_repo
from orbitkb.db.repositories import verification as verification_repo

SAMPLE_RESULT = {
    "primary": [{"service": "checkout-service", "reason": "owns checkout", "confidence": 0.9, "evidence": []}],
    "secondary": [], "no_change_hint": [], "external_integrations": [], "unmapped_internal_hint": [],
}


def test_record_and_get_verification(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    run_id = change_surface_repo.record_change_surface_run(conn, "task", "claude", SAMPLE_RESULT)

    verification_id = verification_repo.record_verification(
        conn, run_id, repository="checkout-repo", since_commit="abc123",
        precision=1.0, recall=0.5,
        true_positives=["checkout-service"], false_positives=[], false_negatives=["payments-service"],
    )

    assert verification_id > 0
    stored = verification_repo.get_verification(conn, verification_id)
    assert stored["run_id"] == run_id
    assert stored["precision"] == 1.0
    assert stored["recall"] == 0.5


def test_latest_verification_for_run(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    run_id = change_surface_repo.record_change_surface_run(conn, "task", "claude", SAMPLE_RESULT)

    verification_repo.record_verification(
        conn, run_id, repository="checkout-repo", since_commit="c1",
        precision=0.5, recall=0.5, true_positives=[], false_positives=[], false_negatives=[],
    )
    verification_repo.record_verification(
        conn, run_id, repository="checkout-repo", since_commit="c2",
        precision=1.0, recall=1.0, true_positives=[], false_positives=[], false_negatives=[],
    )

    latest = verification_repo.latest_verifications(conn, limit=5)
    assert latest[0]["since_commit"] == "c2"  # most recent first
