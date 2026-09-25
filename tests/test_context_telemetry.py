"""Privacy-safe calibration metadata for compact change-context responses."""
from orbitkb.db.repositories import context_telemetry as telemetry_repo
from orbitkb.generation.token_budget import TokenMeasurement
from orbitkb.mcp import queries
from tests.test_change_surface import FakeBackend, _build_pix_fixture


def _context(conn):
    return queries.get_change_context(conn, FakeBackend({
        "primary": [
            {"service": "checkout-service", "reason": "owns checkout", "confidence": 0.9},
            {"service": "payments-service", "reason": "authorizes payment", "confidence": 0.8},
        ],
        "secondary": [{"service": "order-service", "reason": "observes payment", "confidence": 0.5}],
        "no_change": [],
    }), "Add a payment method", max_services=1)


def test_context_records_only_budget_metadata_and_ranked_service_ids(tmp_path):
    conn = _build_pix_fixture(tmp_path / "telemetry.db")

    result = _context(conn)

    assert result["telemetry"]["recorded"] is True
    row = telemetry_repo.get_run(conn, result["telemetry"]["run_id"])
    assert row["requested_budget"] == 1
    assert row["returned_cards"] == 1
    assert row["candidate_count"] == 3
    assert row["truncated"] == 1
    assert row["response_bytes"] > 0
    assert row["estimated_tokens"] > 0
    assert row["token_measurement"] == "byte_estimate"
    assert "Add a payment method" not in str(dict(row))
    assert "checkout-service" not in row["included_service_ids_json"]


def test_context_telemetry_records_and_aggregates_the_token_measurement(tmp_path, monkeypatch):
    conn = _build_pix_fixture(tmp_path / "tokenizer-telemetry.db")
    monkeypatch.setattr(
        queries, "measure_json_tokens", lambda _response: TokenMeasurement(37, "tiktoken:o200k_base"),
    )

    result = _context(conn)

    row = telemetry_repo.get_run(conn, result["telemetry"]["run_id"])
    assert row["estimated_tokens"] == 37
    assert row["token_measurement"] == "tiktoken:o200k_base"
    assert queries.get_context_budget_metrics(conn)["token_measurements"] == [
        {"measurement": "tiktoken:o200k_base", "runs": 1},
    ]


def test_context_telemetry_failure_never_blocks_the_context_response(tmp_path, monkeypatch):
    conn = _build_pix_fixture(tmp_path / "nonblocking.db")
    monkeypatch.setattr(telemetry_repo, "record_run", lambda *_: (_ for _ in ()).throw(RuntimeError("no db")))

    result = _context(conn)

    assert result["services"]
    assert result["telemetry"] == {"recorded": False}


def test_context_feedback_and_aggregates_use_outcomes_without_retaining_note_text(tmp_path):
    conn = _build_pix_fixture(tmp_path / "feedback.db")
    context = _context(conn)
    run_id = context["telemetry"]["run_id"]

    feedback = queries.record_change_context_feedback(
        conn, run_id, "insufficient", note="Need the fraud service too", missing_services=["order-service"],
    )

    assert feedback == {"ok": True, "note_recorded": True}
    stored = telemetry_repo.list_feedback(conn, run_id)[0]
    assert stored["outcome"] == "insufficient"
    assert stored["note_digest"] is not None
    assert "fraud" not in str(dict(stored))
    metrics = queries.get_context_budget_metrics(conn)
    assert metrics["runs"] == 1
    assert metrics["truncation_rate"] == 1.0
    assert metrics["sufficiency"]["insufficient"] == 1
    assert metrics["recommendation"]["status"] == "insufficient_history"


def test_context_query_execution_is_correlated_to_recommendations(tmp_path):
    conn = _build_pix_fixture(tmp_path / "executions.db")
    context = _context(conn)
    run_id = context["telemetry"]["run_id"]

    assert queries.record_context_query_execution(conn, run_id, "describe_api", service="checkout-service") == {"ok": True}
    metrics = queries.get_context_budget_metrics(conn)
    assert metrics["recommended_queries"] >= 1
    assert metrics["executed_recommended_queries"] == 1
