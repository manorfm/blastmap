"""Contract tests for deterministic assessment of a persisted change plan against Git."""
import subprocess
from pathlib import Path

from jsonschema import validate

from orbitkb.analysis.models import (
    AnalysisResult,
    EntryPoint,
    ErrorContract,
    Evidence,
    StaticServiceCall,
)
from orbitkb.db.connection import open_db
from orbitkb.db.repositories import change_plans
from orbitkb.db.repositories import ci_commands as ci_commands_repo
from orbitkb.db.repositories import flows as flows_repo
from orbitkb.db.repositories import repositories as repositories_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.generation.change_assessment import changed_files_touch_service_roots
from orbitkb.generation.llm_harness import load_schema
from orbitkb.mcp import queries
from tests.test_change_surface import FakeBackend


def test_assessment_recognizes_a_changed_file_in_a_repository_root_service(tmp_path: Path):
    repository_root = tmp_path / "repository"
    repository_root.mkdir()

    assert changed_files_touch_service_roots(
        repository_root, ["src/checkout.py"], {"checkout-service": repository_root},
    ) is True


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
    (payments_root / "controller.ts").write_text("authorize()\n")
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
        conn, "payments-service", str(payments_root), "node-ts", repository_id=repository_id,
    )
    evidence = Evidence("client.py", 1, 1)
    controller_evidence = Evidence("controller.ts", 1, 1)
    flows_repo.replace_analysis(conn, checkout_id, AnalysisResult(static_service_calls=[
        StaticServiceCall(
            source="CheckoutClient.authorize", target_service="payments-service", protocol="http",
            target_method="POST", target_path="/authorizations", evidence=evidence,
        ),
    ]))
    flows_repo.replace_analysis(conn, payments_id, AnalysisResult(entrypoints=[
        EntryPoint("http", "POST", "/authorizations", "PaymentsController.authorize", controller_evidence),
    ], error_contracts=[
        ErrorContract(
            source="controller.authorize", role="maps", error_kind="conflict",
            internal_type="AuthorizationDeclined", protocol="http", transport_code="409",
            public_code="AUTHORIZATION_DECLINED", exposes_internal_detail=False,
            retryability="not_retryable", evidence=controller_evidence,
        ),
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
    assert result["ci_validation_commands"] == []
    assert result["ci_validation_results"] == []
    assert result["pending_validation"] == [{
        "change_unit_id": unit_id,
        "contracts": ["POST /authorizations"],
        "dependencies": ["payments-service"],
        "checks": ["verify client and payments-service agree on POST /authorizations"],
    }]
    validate(result, load_schema("assess_working_change"))


def test_assess_working_change_recommends_indexed_ci_commands_for_changed_planned_services(tmp_path: Path):
    conn, plan, since_commit = _plan_with_a_resolved_http_unit(tmp_path / "repository")
    repository_id = repositories_repo.get_repository_by_name(conn, "commerce")["id"]
    ci_commands_repo.replace_ci_commands(conn, repository_id, [
        {
            "workflow_path": ".github/workflows/ci.yml", "kind": "test", "command": "pytest -q",
            "evidence": {"file": ".github/workflows/ci.yml", "start_line": 5, "end_line": 5},
        },
        {
            "workflow_path": ".github/workflows/ci.yml", "kind": "build", "command": "npm run build",
            "evidence": {"file": ".github/workflows/ci.yml", "start_line": 6, "end_line": 6},
        },
        {
            "workflow_path": ".github/workflows/ci.yml", "kind": "migration", "command": "npm run migrate",
            "evidence": {"file": ".github/workflows/ci.yml", "start_line": 7, "end_line": 7},
        },
    ])
    repository_root = tmp_path / "repository"
    (repository_root / "checkout-service" / "client.py").write_text("request_authorization_v2()\n")
    assert queries.record_ci_validation_result(
        conn, plan["plan_id"], "commerce", ".github/workflows/ci.yml", 5, "passed", 900,
    ) == {"ok": True, "status": "passed", "duration_ms": 900}

    result = queries.assess_working_change(conn, plan["plan_id"], "commerce", since_commit)

    assert result["ci_validation_commands"] == [
        {"kind": "test", "command": "pytest -q", "workflow_path": ".github/workflows/ci.yml", "start_line": 5},
        {"kind": "build", "command": "npm run build", "workflow_path": ".github/workflows/ci.yml", "start_line": 6},
    ]
    assert result["ci_validation_results"] == [{
        "kind": "test", "command": "pytest -q", "workflow_path": ".github/workflows/ci.yml",
        "status": "passed", "duration_ms": 900,
    }]
    validate(result, load_schema("assess_working_change"))


def test_record_ci_validation_result_accepts_only_an_indexed_safe_test_or_build_command(tmp_path: Path):
    conn, plan, _since_commit = _plan_with_a_resolved_http_unit(tmp_path / "repository")
    repository_id = repositories_repo.get_repository_by_name(conn, "commerce")["id"]
    ci_commands_repo.replace_ci_commands(conn, repository_id, [
        {
            "workflow_path": ".github/workflows/ci.yml", "kind": "test", "command": "pytest -q",
            "evidence": {"file": ".github/workflows/ci.yml", "start_line": 5, "end_line": 5},
        },
        {
            "workflow_path": ".github/workflows/ci.yml", "kind": "migration", "command": "npm run migrate",
            "evidence": {"file": ".github/workflows/ci.yml", "start_line": 6, "end_line": 6},
        },
    ])

    assert queries.record_ci_validation_result(
        conn, plan["plan_id"], "commerce", ".github/workflows/ci.yml", 5, "passed", 900,
    ) == {"ok": True, "status": "passed", "duration_ms": 900}
    assert queries.record_ci_validation_result(
        conn, plan["plan_id"], "commerce", ".github/workflows/ci.yml", 6, "passed",
    ) == {"error": "indexed command is not a test or build validation"}
    assert queries.record_ci_validation_result(
        conn, plan["plan_id"], "commerce", ".github/workflows/ci.yml", 5, "skipped",
    ) == {"error": "status must be 'passed' or 'failed'"}
    assert queries.record_ci_validation_result(
        conn, plan["plan_id"], "commerce", ".github/workflows/ci.yml", 5, "passed", -1,
    ) == {"error": "duration_ms must be an integer between 0 and 86400000"}


def test_assessment_does_not_match_a_result_from_a_deduplicated_command_at_another_line(tmp_path: Path):
    conn, plan, since_commit = _plan_with_a_resolved_http_unit(tmp_path / "repository")
    repository_id = repositories_repo.get_repository_by_name(conn, "commerce")["id"]
    ci_commands_repo.replace_ci_commands(conn, repository_id, [
        {
            "workflow_path": ".github/workflows/ci.yml", "kind": "test", "command": "pytest -q",
            "evidence": {"file": ".github/workflows/ci.yml", "start_line": 5, "end_line": 5},
        },
        {
            "workflow_path": ".github/workflows/ci.yml", "kind": "test", "command": "pytest -q",
            "evidence": {"file": ".github/workflows/ci.yml", "start_line": 6, "end_line": 6},
        },
    ])
    assert queries.record_ci_validation_result(
        conn, plan["plan_id"], "commerce", ".github/workflows/ci.yml", 6, "passed",
    ) == {"ok": True, "status": "passed", "duration_ms": None}
    (tmp_path / "repository" / "checkout-service" / "client.py").write_text("changed\n")

    result = queries.assess_working_change(conn, plan["plan_id"], "commerce", since_commit)

    assert result["ci_validation_results"] == []


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


def test_assess_working_change_flags_changed_public_error_contract_evidence(tmp_path: Path):
    conn, plan, since_commit = _plan_with_a_resolved_http_unit(tmp_path / "repository")
    repository_root = tmp_path / "repository"
    (repository_root / "payments-service" / "controller.ts").write_text("authorize_v2()\n")

    result = queries.assess_working_change(conn, plan["plan_id"], "commerce", since_commit)

    assert result["public_error_contracts_at_risk"] == [{
        "service": "payments-service",
        "symbol": "controller.authorize",
        "protocol": "http",
        "transport_code": "409",
        "public_code": "AUTHORIZATION_DECLINED",
        "changed_file": "payments-service/controller.ts",
        "evidence": [{"file": "controller.ts", "start_line": 1, "end_line": 1}],
    }]
    validate(result, load_schema("assess_working_change"))


def test_assess_working_change_detects_a_changed_public_error_status(tmp_path: Path):
    conn, plan, since_commit = _plan_with_a_resolved_http_unit(tmp_path / "repository")
    repository_root = tmp_path / "repository"
    (repository_root / "payments-service" / "controller.ts").write_text(
        '''import express from "express";
const app = express();
function authorize(req: Request, res: Response) { return res.status(422).json({}); }
app.post("/authorizations", authorize);
''',
        encoding="utf-8",
    )

    result = queries.assess_working_change(conn, plan["plan_id"], "commerce", since_commit)

    assert result["public_error_contract_breaks"] == [{
        "service": "payments-service",
        "symbol": "controller.authorize",
        "protocol": "http",
        "previous": {"transport_code": "409", "public_code": "AUTHORIZATION_DECLINED"},
        "current": {"transport_code": "422", "public_code": None},
        "changed_file": "payments-service/controller.ts",
        "evidence": [
            {"file": "controller.ts", "start_line": 1, "end_line": 1},
            {"file": "controller.ts", "start_line": 3, "end_line": 3},
        ],
    }]
    validate(result, load_schema("assess_working_change"))
