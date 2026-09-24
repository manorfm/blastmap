from pathlib import Path

from orbitkb.db.connection import open_db
from orbitkb.db.repositories import (
    kubernetes_configuration as kubernetes_configuration_repo,
)
from orbitkb.db.repositories import repositories as repositories_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.iac.models import KubernetesConfigurationBinding


def test_kubernetes_configuration_bindings_are_replaced_and_resolved_to_services(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    repository_id = repositories_repo.ensure_repository(conn, "shop", "/tmp/shop")
    service_id = services_repo.ensure_service(
        conn, "orders-service", "/tmp/shop/orders-service", "node-ts", repository_id=repository_id,
    )
    binding = KubernetesConfigurationBinding(
        environment_key="ORDERS_TOPIC", source_kind="config_map", source_name="orders-config",
        source_key="orders-topic", workload_kind="Deployment", workload_name="orders", container_name="api",
        file_path="orders-service/deployment.yaml", start_line=12, end_line=17,
        matched_service_name="orders-service",
    )

    kubernetes_configuration_repo.replace_kubernetes_configuration_bindings(conn, repository_id, [binding])

    assert [dict(row) for row in kubernetes_configuration_repo.list_kubernetes_configuration_bindings_for_service(
        conn, service_id,
    )] == [{
        "environment_key": "ORDERS_TOPIC", "source_kind": "config_map", "source_name": "orders-config",
        "source_key": "orders-topic", "workload_kind": "Deployment", "workload_name": "orders",
        "container_name": "api", "file_path": "orders-service/deployment.yaml", "start_line": 12,
        "end_line": 17,
    }]

    kubernetes_configuration_repo.replace_kubernetes_configuration_bindings(conn, repository_id, [])

    assert kubernetes_configuration_repo.list_kubernetes_configuration_bindings_for_service(conn, service_id) == []
