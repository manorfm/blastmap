"""Contract tests for deterministic assessment of a persisted change plan against Git."""
import subprocess
from pathlib import Path

from jsonschema import validate

from orbitkb.analysis.models import (
    AnalysisResult,
    EntryPoint,
    Evidence,
    StaticServiceCall,
)
from orbitkb.db.connection import open_db
from orbitkb.db.repositories import change_plans
from orbitkb.db.repositories import flows as flows_repo
from orbitkb.db.repositories import repositories as repositories_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.generation.llm_harness import load_schema
from orbitkb.mcp import queries
from tests.test_change_surface import FakeBackend


def _plan_with_a_resolved_http_unit(root: Path):
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    checkout_root = root / "checkout-service"
    payments_root = root / "payments-service"
    checkout_root.mkdir()
    payments_root.mkdir()
    (checkout_root / "client.py").write_text("request_authorization()\n")
    (payments_root / "controller.py").write_text("authorize()\n")
    (root / "README.md").write_text("before\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)
    since_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True,
    ).stdout.strip()

    conn = open_db(root.parent / "assessment.db")
    repository_id = repositories_repo.ensure_repository(conn, "commerce", str(root))
    checkout_id = services_repo.ensure_service(
        conn, "checkout-service", str(checkout_root), "python", repository_id=repository_id,
    )
    payments_id = services_repo.ensure_service(
        conn, "payments-service", str(payments_root), "python", repository_id=repository_id,
    )
    evidence = Evidence("client.py", 1, 1)
    flows_repo.replace_analysis(conn, checkout_id, AnalysisResult(static_service_calls=[
        StaticServiceCall(
            source="CheckoutClient.authorize", target_service="payments-service", protocol="http",
            target_method="POST", target_path="/authorizations", evidence=evidence,
        ),
    ]))
    flows_repo.replace_analysis(conn, payments_id, AnalysisResult(entrypoints=[
        EntryPoint("http", "POST", "/authorizations", "PaymentsController.authorize", evidence),
    ]))
    plan = queries.plan_change(conn, FakeBackend({
        "primary": [{"service": "checkout-service", "reason": "owns checkout", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    }), "Add a payment method", hint_services=["checkout-service"], repository="commerce")
    return conn, plan, since_commit


def test_assess_working_change_reports_an_omitted_evidence_backed_unit(tmp_path: Path):
    conn, plan, since_commit = _plan_with_a_resolved_http_unit(tmp_path / "repository")
    repository_root = tmp_path / "repository"
    (repository_root / "README.md").write_text("after\n")

    result = queries.assess_working_change(conn, plan["plan_id"], "commerce", since_commit)

    unit_id = "http-contract:checkout-service:payments-service:POST:/authorizations"
    assert result["covered_change_units"] == []
    assert result["omitted_change_units"] == [{
        "id": unit_id,
        "evidence_files": ["checkout-service/client.py"],
    }]
    assert result["files_outside_planned_surface"] == ["README.md"]
    assert result["pending_validation"] == [{
        "change_unit_id": unit_id,
        "contracts": ["POST /authorizations"],
        "dependencies": ["payments-service"],
        "checks": ["verify client and payments-service agree on POST /authorizations"],
    }]
    validate(result, load_schema("assess_working_change"))


def test_assess_working_change_never_labels_a_unit_without_source_evidence_as_omitted(tmp_path: Path):
    conn, _plan, since_commit = _plan_with_a_resolved_http_unit(tmp_path / "repository")
    plan_id = change_plans.record_plan(conn, None, "ready", 100, [], [{
        "id": "event-contract:checkout-service:payment_authorized",
        "service": "checkout-service",
        "target": {"role": "contract", "symbol": "message.publish:payment_authorized", "evidence": []},
        "action": "validate",
        "reason": "preserve compatibility",
        "preconditions": [],
        "related_contracts": ["payment_authorized"],
        "dependencies": [],
        "validation": ["verify compatibility"],
        "confidence": 1.0,
        "evidence": [],
    }])

    result = queries.assess_working_change(conn, f"cp_{plan_id}", "commerce", since_commit)

    assert result["omitted_change_units"] == []
    assert result["unassessable_change_units"] == [{
        "id": "event-contract:checkout-service:payment_authorized",
        "reason": "the change unit has no source evidence file",
    }]
