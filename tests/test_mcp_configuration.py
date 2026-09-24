"""Contract coverage for source-proven configuration bindings exposed to agents."""

from orbitkb.analysis.models import AnalysisResult, ConfigurationBinding, Evidence
from orbitkb.db.connection import open_db
from orbitkb.db.repositories import (
    flows,
    kubernetes_configuration,
    repositories,
    services,
)
from orbitkb.iac.models import (
    KubernetesConfigurationBinding,
    KubernetesConfigurationKeyMismatch,
    KubernetesConfigurationSourceUnknown,
)
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


def test_describe_runtime_configuration_marks_a_source_proven_missing_declared_key(tmp_path):
    conn = open_db(tmp_path / "runtime-configuration-mismatch.db")
    repository_id = repositories.ensure_repository(conn, "shop", "/repos/shop")
    services.ensure_service(conn, "orders", "/repos/shop/orders", "node-ts", repository_id=repository_id)
    binding = KubernetesConfigurationBinding(
        environment_key="ORDERS_TOPIC", source_kind="config_map", source_name="orders-config",
        source_key="orders-topic", workload_kind="Deployment", workload_name="orders", container_name="api",
        file_path="deploy/orders.yaml", start_line=12, end_line=17, matched_service_name="orders",
    )
    kubernetes_configuration.replace_kubernetes_configuration_bindings(conn, repository_id, [binding])
    kubernetes_configuration.replace_kubernetes_configuration_key_mismatches(conn, repository_id, [
        KubernetesConfigurationKeyMismatch(
            environment_key="ORDERS_TOPIC", source_kind="config_map", source_name="orders-config",
            source_key="orders-topic", reference_file_path="deploy/orders.yaml", reference_start_line=12,
            reference_end_line=17, declaration_file_path="deploy/config.yaml", declaration_start_line=1,
            declaration_end_line=7, matched_service_name="orders",
        ),
    ])

    result = queries.describe_runtime_configuration(conn, "orders")

    assert result["bindings"][0]["declaration"] == {
        "status": "key_not_declared",
        "evidence": {"file": "deploy/config.yaml", "start_line": 1, "end_line": 7},
    }


def test_describe_runtime_configuration_marks_a_source_not_declared_locally_as_unknown(tmp_path):
    conn = open_db(tmp_path / "runtime-configuration-source-unknown.db")
    repository_id = repositories.ensure_repository(conn, "shop", "/repos/shop")
    services.ensure_service(conn, "orders", "/repos/shop/orders", "node-ts", repository_id=repository_id)
    binding = KubernetesConfigurationBinding(
        environment_key="ORDERS_TOPIC", source_kind="config_map", source_name="external-config",
        source_key="orders-topic", workload_kind="Deployment", workload_name="orders", container_name="api",
        file_path="deploy/orders.yaml", start_line=12, end_line=17, matched_service_name="orders",
    )
    kubernetes_configuration.replace_kubernetes_configuration_bindings(conn, repository_id, [binding])
    kubernetes_configuration.replace_kubernetes_configuration_source_unknowns(conn, repository_id, [
        KubernetesConfigurationSourceUnknown(
            environment_key="ORDERS_TOPIC", source_kind="config_map", source_name="external-config",
            source_key="orders-topic", reference_file_path="deploy/orders.yaml", reference_start_line=12,
            reference_end_line=17, matched_service_name="orders",
        ),
    ])

    result = queries.describe_runtime_configuration(conn, "orders")

    assert result["bindings"][0]["declaration"] == {"status": "not_declared_locally"}


def test_describe_configuration_links_matching_code_and_kubernetes_environment_bindings(tmp_path):
    conn = open_db(tmp_path / "configuration-links.db")
    repository_id = repositories.ensure_repository(conn, "shop", "/repos/shop")
    service_id = services.ensure_service(
        conn, "orders", "/repos/shop/orders", "node-ts", repository_id=repository_id,
    )
    flows.replace_analysis(conn, service_id, AnalysisResult(configuration_bindings=[
        ConfigurationBinding("publisher.publish", "ORDERS_TOPIC", "environment", False, Evidence("publisher.ts", 3, 3)),
    ]))
    kubernetes_configuration.replace_kubernetes_configuration_bindings(conn, repository_id, [
        KubernetesConfigurationBinding(
            environment_key="ORDERS_TOPIC", source_kind="config_map", source_name="orders-config",
            source_key="orders-topic", workload_kind="Deployment", workload_name="orders", container_name="api",
            file_path="deploy/orders.yaml", start_line=12, end_line=17, matched_service_name="orders",
        ),
    ])

    result = queries.describe_configuration(conn, "orders")

    assert result["bindings"] == [{
        "source": "publisher.publish",
        "key": "ORDERS_TOPIC",
        "kind": "environment",
        "sensitive": False,
        "evidence": {"file": "publisher.ts", "start_line": 3, "end_line": 3},
        "runtime_sources": {
            "count": 1,
            "references": [{
                "source": {"kind": "config_map", "name": "orders-config", "key": "orders-topic"},
                "workload": {"kind": "Deployment", "name": "orders", "container": "api"},
                "evidence": {"file": "deploy/orders.yaml", "start_line": 12, "end_line": 17},
            }],
            "truncated": False,
        },
    }]


def test_describe_configuration_caps_runtime_sources_per_environment_key(tmp_path):
    conn = open_db(tmp_path / "configuration-link-cap.db")
    repository_id = repositories.ensure_repository(conn, "shop", "/repos/shop")
    service_id = services.ensure_service(
        conn, "orders", "/repos/shop/orders", "node-ts", repository_id=repository_id,
    )
    flows.replace_analysis(conn, service_id, AnalysisResult(configuration_bindings=[
        ConfigurationBinding("client.create", "PAYMENTS_URL", "environment", False, Evidence("client.ts", 3, 3)),
    ]))
    kubernetes_configuration.replace_kubernetes_configuration_bindings(conn, repository_id, [
        KubernetesConfigurationBinding(
            environment_key="PAYMENTS_URL", source_kind="config_map", source_name=f"orders-config-{index}",
            source_key="payments-url", workload_kind="Deployment", workload_name=f"orders-{index}",
            container_name="api", file_path=f"deploy/orders-{index}.yaml", start_line=12, end_line=17,
            matched_service_name="orders",
        )
        for index in range(4)
    ])

    sources = queries.describe_configuration(conn, "orders")["bindings"][0]["runtime_sources"]

    assert sources["count"] == 4
    assert len(sources["references"]) == 3
    assert sources["truncated"] is True
