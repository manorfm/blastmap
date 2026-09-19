from pathlib import Path

from impactmesh.db.connection import open_db
from impactmesh.db.repositories import architecture as architecture_repo


def test_start_run_and_record_finding(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    run_id = architecture_repo.start_run(conn, services_indexed=3)

    architecture_repo.record_finding(
        conn, run_id, "cycle", "warning", ["a-service", "b-service"], "a-service and b-service form a cycle",
        detail={"hops": 2},
    )

    findings = architecture_repo.list_findings(conn, run_id)
    assert len(findings) == 1
    assert findings[0]["kind"] == "cycle"
    assert findings[0]["severity"] == "warning"
    assert findings[0]["services_json"] == '["a-service", "b-service"]'
    assert findings[0]["detail_json"] == '{"hops": 2}'


def test_findings_are_scoped_per_run(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    run_1 = architecture_repo.start_run(conn, services_indexed=2)
    run_2 = architecture_repo.start_run(conn, services_indexed=2)
    architecture_repo.record_finding(conn, run_1, "cycle", "warning", ["a"], "r1")
    architecture_repo.record_finding(conn, run_2, "fan_in", "info", ["b"], "r2")

    assert len(architecture_repo.list_findings(conn, run_1)) == 1
    assert len(architecture_repo.list_findings(conn, run_2)) == 1


def test_latest_run_id(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    assert architecture_repo.latest_run_id(conn) is None

    architecture_repo.start_run(conn, services_indexed=1)
    second = architecture_repo.start_run(conn, services_indexed=1)

    assert architecture_repo.latest_run_id(conn) == second


def test_previous_run_id_returns_the_run_before_the_given_one(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    first = architecture_repo.start_run(conn, services_indexed=1)
    second = architecture_repo.start_run(conn, services_indexed=1)
    third = architecture_repo.start_run(conn, services_indexed=1)

    assert architecture_repo.previous_run_id(conn, third) == second
    assert architecture_repo.previous_run_id(conn, second) == first


def test_previous_run_id_is_none_for_the_first_run_ever(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    first = architecture_repo.start_run(conn, services_indexed=1)

    assert architecture_repo.previous_run_id(conn, first) is None
