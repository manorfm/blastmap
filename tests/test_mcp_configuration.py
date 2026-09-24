"""Contract coverage for source-proven configuration bindings exposed to agents."""

from orbitkb.analysis.models import AnalysisResult, ConfigurationBinding, Evidence
from orbitkb.db.connection import open_db
from orbitkb.db.repositories import (
    flows,
    kubernetes_configuration,
    repositories,
    services,
)
from orbitkb.iac.models import KubernetesConfigurationBinding
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


def test_describe_runtime_configuration_returns_kubernetes_references_without_values(tmp_path):
    conn = open_db(tmp_path / "runtime-configuration.db")
    repository_id = repositories.ensure_repository(conn, "shop", "/repos/shop")
    services.ensure_service(
        conn, "orders", "/repos/shop/orders", "node-ts", repository_id=repository_id,
    )
    kubernetes_configuration.replace_kubernetes_configuration_bindings(conn, repository_id, [
        KubernetesConfigurationBinding(
            environment_key="STRIPE_SECRET_KEY", source_kind="secret", source_name="payments-secrets",
            source_key="stripe-key", workload_kind="Deployment", workload_name="orders", container_name="api",
            file_path="deploy/orders.yaml", start_line=12, end_line=17, matched_service_name="orders",
        ),
    ])

    result = queries.describe_runtime_configuration(conn, "orders")

    assert result == {
        "service": "orders",
        "repository": "shop",
        "bindings": [{
            "environment_key": "STRIPE_SECRET_KEY",
            "source": {"kind": "secret", "name": "payments-secrets", "key": "stripe-key"},
            "workload": {"kind": "Deployment", "name": "orders", "container": "api"},
            "evidence": {"file": "deploy/orders.yaml", "start_line": 12, "end_line": 17},
        }],
        "total": 1,
        "truncated": False,
    }
