from pathlib import Path

from blastmap.db import repository
from blastmap.db.connection import open_db
from blastmap.mcp import queries

SAMPLE_RESULT = {
    "primary": [{"service": "checkout-service", "reason": "owns checkout", "confidence": 0.9, "evidence": []}],
    "secondary": [{"service": "order-service", "reason": "consumes confirmation", "confidence": 0.4, "evidence": []}],
    "no_change_hint": [{"service": "notification-service", "reason": "unrelated", "confidence": 0.8, "evidence": []}],
    "external_integrations": [{"service": "Stripe API", "via_service": "payments-service", "reason": "charges card", "confidence": 0.85, "evidence": []}],
    "unmapped_internal_hint": [{"service": "shipping-service", "via_service": "order-service", "reason": "schedules delivery", "confidence": 0.6, "evidence": []}],
}


def test_record_change_surface_run_persists_all_buckets(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")

    run_id = repository.record_change_surface_run(conn, "Add Pix support", "claude", SAMPLE_RESULT)

    findings = repository.list_change_surface_findings(conn, run_id)
    roles = {(f["service"], f["role"]) for f in findings}
    assert ("checkout-service", "primary") in roles
    assert ("order-service", "secondary") in roles
    assert ("notification-service", "no_change") in roles
    assert ("Stripe API", "external_integration") in roles
    assert ("shipping-service", "unmapped_internal") in roles

    run = repository.get_change_surface_run(conn, run_id)
    assert run["task_text"] == "Add Pix support"
    assert run["backend"] == "claude"


def test_list_change_surface_runs_orders_most_recent_first(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    first = repository.record_change_surface_run(conn, "first task", "claude", SAMPLE_RESULT)
    second = repository.record_change_surface_run(conn, "second task", "claude", SAMPLE_RESULT)

    runs = repository.list_change_surface_runs(conn, limit=10)

    assert runs[0]["id"] == second
    assert runs[1]["id"] == first


def test_feedback_stats_start_empty_and_accumulate(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    run_id = repository.record_change_surface_run(conn, "task", "claude", SAMPLE_RESULT)

    assert repository.get_feedback_stats(conn, "checkout-service") == {"confirmed": 0, "rejected": 0}

    repository.record_change_surface_feedback(conn, run_id, "checkout-service", "confirmed")
    repository.record_change_surface_feedback(conn, run_id, "checkout-service", "confirmed")
    repository.record_change_surface_feedback(conn, run_id, "checkout-service", "rejected")

    assert repository.get_feedback_stats(conn, "checkout-service") == {"confirmed": 2, "rejected": 1}
    assert repository.get_feedback_stats(conn, "some-other-service") == {"confirmed": 0, "rejected": 0}


def test_record_feedback_query_validates_run_and_service(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    run_id = repository.record_change_surface_run(conn, "task", "claude", SAMPLE_RESULT)

    ok = queries.record_change_surface_feedback(conn, run_id, "checkout-service", "confirmed")
    assert ok == {"ok": True}
    assert repository.get_feedback_stats(conn, "checkout-service") == {"confirmed": 1, "rejected": 0}

    bad_run = queries.record_change_surface_feedback(conn, 99999, "checkout-service", "confirmed")
    assert "error" in bad_run

    bad_service = queries.record_change_surface_feedback(conn, run_id, "never-mentioned-service", "confirmed")
    assert "error" in bad_service

    bad_outcome = queries.record_change_surface_feedback(conn, run_id, "checkout-service", "maybe")
    assert "error" in bad_outcome
