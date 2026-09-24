"""Contract coverage for source-proven configuration bindings exposed to agents."""

from orbitkb.analysis.models import AnalysisResult, ConfigurationBinding, Evidence
from orbitkb.db.connection import open_db
from orbitkb.db.repositories import flows, services
from orbitkb.mcp import queries


def test_describe_configuration_returns_paginated_environment_bindings_without_values(tmp_path):
    conn = open_db(tmp_path / "configuration.db")
    service_id = services.ensure_service(conn, "orders", "/repos/orders", "node-ts")
    flows.replace_analysis(conn, service_id, AnalysisResult(configuration_bindings=[
        ConfigurationBinding("publisher.publish", "ORDERS_TOPIC", "environment", False, Evidence("publisher.ts", 3, 3)),
        ConfigurationBinding("publisher.publish", "STRIPE_SECRET_KEY", "environment", True, Evidence("publisher.ts", 4, 4)),
    ]))

    result = queries.describe_configuration(conn, "orders", limit=1, offset=1)

    assert result == {
        "service": "orders",
        "repository": None,
        "bindings": [{
            "source": "publisher.publish",
            "key": "STRIPE_SECRET_KEY",
            "kind": "environment",
            "sensitive": True,
            "evidence": {"file": "publisher.ts", "start_line": 4, "end_line": 4},
        }],
        "total": 2,
        "truncated": False,
    }
