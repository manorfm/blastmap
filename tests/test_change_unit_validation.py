from jsonschema import validate

from orbitkb.generation.llm_harness import load_schema
from orbitkb.mcp import queries
from tests.test_change_assessment import _plan_with_a_resolved_http_unit


def test_change_unit_validation_records_only_a_persisted_check_status(tmp_path):
    conn, plan, _since_commit = _plan_with_a_resolved_http_unit(tmp_path / "repository")
    unit_id = "http-contract:checkout-service:payments-service:POST:/authorizations"

    before = queries.describe_change_unit(conn, plan["plan_id"], unit_id)

    assert before["validation_status"] == {
        "summary": {"total": 1, "passed": 0, "failed": 0, "pending": 1},
        "checks": [{"index": 0, "status": "pending"}],
    }
    assert queries.record_change_unit_validation_result(
        conn, plan["plan_id"], unit_id, 0, "passed",
    ) == {"ok": True, "status": "passed"}
    assert queries.record_change_unit_validation_result(
        conn, plan["plan_id"], unit_id, 1, "passed",
    ) == {"error": "check_index must identify a persisted validation check"}
    assert queries.record_change_unit_validation_result(
        conn, plan["plan_id"], unit_id, 0, "skipped",
    ) == {"error": "status must be 'passed' or 'failed'"}

    after = queries.describe_change_unit(conn, plan["plan_id"], unit_id)

    assert after["validation_status"] == {
        "summary": {"total": 1, "passed": 1, "failed": 0, "pending": 0},
        "checks": [{"index": 0, "status": "passed"}],
    }
    validate(after, load_schema("describe_change_unit"))
