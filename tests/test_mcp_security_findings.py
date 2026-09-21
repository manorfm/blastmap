from orbitkb.db.connection import open_db
from orbitkb.db.repositories import security_findings, services
from orbitkb.mcp import queries
from orbitkb.security.findings import SecurityFinding


def test_security_findings_are_progressively_disclosed_per_service(tmp_path):
    conn = open_db(tmp_path / "security-mcp.db")
    service_id = services.ensure_service(conn, "orders", "/repos/orders", "python")
    security_findings.replace_findings(
        conn, service_id, [SecurityFinding("tracked_dotenv", "warning", ".env", 1, ".env is tracked by Git")],
    )

    response = queries.list_security_findings(conn, "orders")

    assert response["findings"] == [
        {"kind": "tracked_dotenv", "severity": "warning", "file": ".env", "line": 1, "reason": ".env is tracked by Git"}
    ]
