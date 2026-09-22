"""Checks the evidence boundaries of OrbitKB's reproducible readiness audit."""
from pathlib import Path

from benchmark.readiness import run_readiness_audit


def test_readiness_audit_reports_deterministic_evidence_and_conditional_limits(tmp_path: Path):
    report = run_readiness_audit(tmp_path)

    assert report.status == "conditional"
    assert report.static_facts_passed is True
    assert report.change_surface_candidates_passed is True
    assert any("container" in item for item in report.conditions)
    assert any("real repository" in item for item in report.conditions)
    assert report.as_dict()["status"] == "conditional"
