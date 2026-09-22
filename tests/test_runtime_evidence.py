"""Runtime observations remain separate from static flow facts."""
from orbitkb.analysis.models import AnalysisResult, Evidence, FlowEdge
from orbitkb.db.connection import open_db
from orbitkb.db.repositories import flows as flows_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.mcp import queries


def test_runtime_evidence_reports_observed_only_and_static_unobserved_without_claiming_dead_code(tmp_path):
    conn = open_db(tmp_path / "runtime.db")
    service_id = services_repo.ensure_service(conn, "orders", "/tmp/orders", "go")
    flows_repo.replace_analysis(conn, service_id, AnalysisResult(edges=[
        FlowEdge("Handler.create", "UseCase.execute", "invokes", Evidence("handler.go", 4, 4)),
    ]))

    ingested = queries.ingest_runtime_evidence(conn, "orders", "otel", [
        {"from": "Handler.create", "to": "UseCase.execute", "kind": "invokes", "count": 8},
        {"from": "UseCase.execute", "to": "Payments.authorize", "kind": "invokes", "count": 3},
    ])
    divergence = queries.describe_runtime_divergence(conn, "orders")

    assert ingested == {"accepted": 2, "rejected": 0}
    assert divergence["observed_only"] == [{
        "from": "UseCase.execute", "to": "Payments.authorize", "kind": "invokes", "count": 3,
    }]
    assert divergence["static_unobserved"] == []
    assert "not proof of dead code" in divergence["unknowns"][0]


def test_runtime_evidence_rejects_payloads_that_could_be_trace_or_code_content(tmp_path):
    conn = open_db(tmp_path / "runtime-invalid.db")
    services_repo.ensure_service(conn, "orders", "/tmp/orders", "go")

    result = queries.ingest_runtime_evidence(conn, "orders", "otel", [
        {"from": "Handler.create", "to": "UseCase.execute", "kind": "invokes", "count": 1, "payload": "secret"},
        {"from": "Handler.create", "to": "UseCase.execute", "kind": "invalid", "count": 1},
    ])

    assert result == {"accepted": 0, "rejected": 2}
