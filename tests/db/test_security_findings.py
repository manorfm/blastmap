from orbitkb.db.connection import open_db
from orbitkb.db.repositories import security_findings, services
from orbitkb.security.findings import SecurityFinding


def test_security_findings_are_replaced_per_service(tmp_path):
    conn = open_db(tmp_path / "security.db")
    service_id = services.ensure_service(conn, "orders", "/repos/orders", "python")

    security_findings.replace_findings(
        conn, service_id, [SecurityFinding("hardcoded_secret", "warning", "settings.py", 3, "Secret-like literal")],
    )

    assert security_findings.list_findings(conn, service_id)[0]["kind"] == "hardcoded_secret"
    security_findings.replace_findings(conn, service_id, [])
    assert security_findings.list_findings(conn, service_id) == []
