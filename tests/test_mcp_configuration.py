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
    KubernetesConfigurationSourceImport,
    KubernetesConfigurationSourceImportUnknown,
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


def test_describe_runtime_configuration_exposes_env_from_sources_with_unknown_key_coverage(tmp_path):
    conn = open_db(tmp_path / "runtime-configuration-env-from.db")
    repository_id = repositories.ensure_repository(conn, "shop", "/repos/shop")
    services.ensure_service(conn, "orders", "/repos/shop/orders", "node-ts", repository_id=repository_id)
    kubernetes_configuration.replace_kubernetes_configuration_source_imports(conn, repository_id, [
        KubernetesConfigurationSourceImport(
            source_kind="config_map", source_name="shared-defaults", prefix="ORDERS_",
            workload_kind="Deployment", workload_name="orders", container_name="api",
            file_path="deploy/orders.yaml", start_line=11, end_line=14, matched_service_name="orders",
        ),
    ])

    result = queries.describe_runtime_configuration(conn, "orders")

    assert result["source_imports"] == [{
        "source": {"kind": "config_map", "name": "shared-defaults"},
        "prefix": "ORDERS_",
        "workload": {"kind": "Deployment", "name": "orders", "container": "api"},
        "evidence": {"file": "deploy/orders.yaml", "start_line": 11, "end_line": 14},
        "key_coverage": "unknown",
    }]
    assert result["source_import_total"] == 1
    assert result["source_import_truncated"] is False
    assert result["unknowns"] == [
        "envFrom imports source keys without explicit per-key references; exact environment keys are not indexed.",
    ]


def test_describe_runtime_configuration_filters_to_selected_workloads(tmp_path):
    conn = open_db(tmp_path / "runtime-configuration-workload-filter.db")
    repository_id = repositories.ensure_repository(conn, "shop", "/repos/shop")
    services.ensure_service(conn, "orders", "/repos/shop/orders", "node-ts", repository_id=repository_id)
    kubernetes_configuration.replace_kubernetes_configuration_bindings(conn, repository_id, [
        KubernetesConfigurationBinding(
            environment_key="API_TOKEN", source_kind="config_map", source_name="api-config", source_key="token",
            workload_kind="Deployment", workload_name="orders", container_name="api",
            file_path="deploy/api.yaml", start_line=5, end_line=8, matched_service_name="orders",
        ),
        KubernetesConfigurationBinding(
            environment_key="WORKER_TOKEN", source_kind="secret", source_name="worker-secrets", source_key="token",
            workload_kind="Deployment", workload_name="orders", container_name="worker",
            file_path="deploy/worker.yaml", start_line=15, end_line=18, matched_service_name="orders",
        ),
    ])
    kubernetes_configuration.replace_kubernetes_configuration_key_mismatches(conn, repository_id, [
        KubernetesConfigurationKeyMismatch(
            environment_key="API_TOKEN", source_kind="config_map", source_name="api-config", source_key="token",
            reference_file_path="deploy/api.yaml", reference_start_line=5, reference_end_line=8,
            declaration_file_path="deploy/api-secrets.yaml", declaration_start_line=1, declaration_end_line=3,
            matched_service_name="orders",
        ),
    ])
    kubernetes_configuration.replace_kubernetes_configuration_source_unknowns(conn, repository_id, [
        KubernetesConfigurationSourceUnknown(
            environment_key="WORKER_TOKEN", source_kind="secret", source_name="worker-secrets", source_key="token",
            reference_file_path="deploy/worker.yaml", reference_start_line=15, reference_end_line=18,
            matched_service_name="orders",
        ),
    ])
    kubernetes_configuration.replace_kubernetes_configuration_source_imports(conn, repository_id, [
        KubernetesConfigurationSourceImport(
            source_kind="config_map", source_name="api-defaults", prefix="API_",
            workload_kind="Deployment", workload_name="orders", container_name="api",
            file_path="deploy/api.yaml", start_line=11, end_line=14, container_role="initialization",
            optional=True, matched_service_name="orders",
        ),
        KubernetesConfigurationSourceImport(
            source_kind="secret", source_name="worker-secrets", prefix=None,
            workload_kind="Deployment", workload_name="orders", container_name="worker",
            file_path="deploy/worker.yaml", start_line=20, end_line=23, container_role="application",
            optional=False, matched_service_name="orders",
        ),
    ])
    kubernetes_configuration.replace_kubernetes_configuration_source_import_unknowns(conn, repository_id, [
        KubernetesConfigurationSourceImportUnknown(
            source_kind="config_map", source_name="api-defaults", prefix="API_",
            reference_file_path="deploy/api.yaml", reference_start_line=11, reference_end_line=14,
            matched_service_name="orders",
        ),
    ])

    result = queries.describe_runtime_configuration(
        conn,
        "orders",
        workloads=[{"kind": "Deployment", "name": "orders", "container": "worker"}],
    )

    assert [item["source"]["name"] for item in result["source_imports"]] == ["worker-secrets"]
    assert result["source_import_total"] == 1
    assert result["source_import_truncated"] is False
    all_selected = queries.describe_runtime_configuration(
        conn,
        "orders",
        workloads=[
            {"kind": "Deployment", "name": "orders", "container": "worker"},
            {"kind": "Deployment", "name": "orders", "container": "api"},
        ],
    )
    assert [item["source"]["name"] for item in all_selected["source_imports"]] == [
        "api-defaults", "worker-secrets",
    ]
    independently_selected = queries.describe_runtime_configuration(
        conn,
        "orders",
        binding_workloads=[{"kind": "Deployment", "name": "orders", "container": "api"}],
        source_import_workloads=[{"kind": "Deployment", "name": "orders", "container": "worker"}],
    )
    assert [item["environment_key"] for item in independently_selected["bindings"]] == ["API_TOKEN"]
    assert [item["source"]["name"] for item in independently_selected["source_imports"]] == ["worker-secrets"]
    key_mismatch_selected = queries.describe_runtime_configuration(
        conn,
        "orders",
        binding_declaration_statuses=["key_not_declared"],
    )
    assert [item["environment_key"] for item in key_mismatch_selected["bindings"]] == ["API_TOKEN"]
    source_unknown_selected = queries.describe_runtime_configuration(
        conn,
        "orders",
        binding_declaration_statuses=["not_declared_locally"],
    )
    assert [item["environment_key"] for item in source_unknown_selected["bindings"]] == ["WORKER_TOKEN"]
    config_map_bindings = queries.describe_runtime_configuration(
        conn,
        "orders",
        binding_source_kinds=["config_map"],
    )
    assert [item["environment_key"] for item in config_map_bindings["bindings"]] == ["API_TOKEN"]
    declaration_selected = queries.describe_runtime_configuration(
        conn,
        "orders",
        source_import_declaration_statuses=["not_declared_locally"],
    )
    assert [item["source"]["name"] for item in declaration_selected["source_imports"]] == ["api-defaults"]
    availability_selected = queries.describe_runtime_configuration(
        conn,
        "orders",
        source_import_availabilities=["required"],
    )
    assert [item["source"]["name"] for item in availability_selected["source_imports"]] == ["worker-secrets"]
    secret_imports = queries.describe_runtime_configuration(
        conn,
        "orders",
        source_import_source_kinds=["secret"],
    )
    assert [item["source"]["name"] for item in secret_imports["source_imports"]] == ["worker-secrets"]
    initialization_imports = queries.describe_runtime_configuration(
        conn,
        "orders",
        source_import_container_roles=["initialization"],
    )
    assert [item["source"]["name"] for item in initialization_imports["source_imports"]] == ["api-defaults"]
    prefixed_imports = queries.describe_runtime_configuration(
        conn,
        "orders",
        source_import_prefixes=["API_"],
    )
    assert [item["source"]["name"] for item in prefixed_imports["source_imports"]] == ["api-defaults"]
    unprefixed_imports = queries.describe_runtime_configuration(
        conn,
        "orders",
        source_import_include_unprefixed=True,
    )
    assert [item["source"]["name"] for item in unprefixed_imports["source_imports"]] == ["worker-secrets"]
    evidence_selected = queries.describe_runtime_configuration(
        conn,
        "orders",
        binding_evidence_files=["deploy/api.yaml"],
        source_import_evidence_files=["deploy/worker.yaml"],
    )
    assert [item["environment_key"] for item in evidence_selected["bindings"]] == ["API_TOKEN"]
    assert [item["source"]["name"] for item in evidence_selected["source_imports"]] == ["worker-secrets"]
    assert evidence_selected["filter_summary"] == {
        "bindings": {"indexed_total": 2, "selected_total": 1},
        "source_imports": {"indexed_total": 2, "selected_total": 1},
    }
    range_selected = queries.describe_runtime_configuration(
        conn,
        "orders",
        binding_evidence_ranges=[{"file": "deploy/api.yaml", "start_line": 6, "end_line": 6}],
        source_import_evidence_ranges=[{"file": "deploy/worker.yaml", "start_line": 21, "end_line": 21}],
    )
    assert [item["environment_key"] for item in range_selected["bindings"]] == ["API_TOKEN"]
    assert [item["source"]["name"] for item in range_selected["source_imports"]] == ["worker-secrets"]
    assert queries.describe_runtime_configuration(conn, "orders", workloads=[]) == {
        "error": "workloads must be a non-empty list of workload identities",
    }
    assert queries.describe_runtime_configuration(
        conn,
        "orders",
        workloads=[{"kind": "Deployment", "name": "orders", "container": "api"}],
        binding_workloads=[{"kind": "Deployment", "name": "orders", "container": "api"}],
    ) == {"error": "workloads cannot be combined with binding_workloads or source_import_workloads"}
    assert queries.describe_runtime_configuration(
        conn,
        "orders",
        source_import_availabilities=["configured"],
    ) == {"error": "source_import_availabilities must contain only: optional, required, unknown"}
    assert queries.describe_runtime_configuration(
        conn,
        "orders",
        binding_declaration_statuses=["declared"],
    ) == {"error": "binding_declaration_statuses must contain only: key_not_declared, not_declared_locally, not_reported"}
    assert queries.describe_runtime_configuration(
        conn,
        "orders",
        binding_source_kinds=["vault"],
    ) == {"error": "binding_source_kinds must contain only: config_map, secret"}
    assert queries.describe_runtime_configuration(
        conn,
        "orders",
        source_import_container_roles=["sidecar"],
    ) == {"error": "source_import_container_roles must contain only: application, initialization, unknown"}
    assert queries.describe_runtime_configuration(
        conn,
        "orders",
        source_import_prefixes=[""],
    ) == {"error": "source_import_prefixes must be a non-empty list of non-empty strings"}
    assert queries.describe_runtime_configuration(
        conn,
        "orders",
        binding_evidence_files=[""],
    ) == {"error": "binding_evidence_files must be a non-empty list of non-empty strings"}
    assert queries.describe_runtime_configuration(
        conn,
        "orders",
        binding_evidence_ranges=[{"file": "deploy/api.yaml", "start_line": 8, "end_line": 7}],
    ) == {"error": "binding_evidence_ranges must be a non-empty list of valid evidence ranges"}
    assert queries.describe_runtime_configuration(
        conn,
        "orders",
        workloads=[{"kind": "Deployment", "name": "orders", "container": "api"}] * 501,
    ) == {"error": "workloads must contain at most 500 workload identities"}


def test_describe_runtime_configuration_marks_an_env_from_source_not_declared_locally(tmp_path):
    conn = open_db(tmp_path / "runtime-configuration-env-from-source-unknown.db")
    repository_id = repositories.ensure_repository(conn, "shop", "/repos/shop")
    services.ensure_service(conn, "orders", "/repos/shop/orders", "node-ts", repository_id=repository_id)
    source_import = KubernetesConfigurationSourceImport(
        source_kind="config_map", source_name="externally-managed-config", prefix=None,
        workload_kind="Deployment", workload_name="orders", container_name="api",
        file_path="deploy/orders.yaml", start_line=11, end_line=13, matched_service_name="orders",
    )
    kubernetes_configuration.replace_kubernetes_configuration_source_imports(conn, repository_id, [source_import])
    kubernetes_configuration.replace_kubernetes_configuration_source_import_unknowns(conn, repository_id, [
        KubernetesConfigurationSourceImportUnknown(
            source_kind="config_map", source_name="externally-managed-config", prefix=None,
            reference_file_path="deploy/orders.yaml", reference_start_line=11, reference_end_line=13,
            matched_service_name="orders",
        ),
    ])

    result = queries.describe_runtime_configuration(conn, "orders")

    assert result["source_imports"][0]["declaration"] == {"status": "not_declared_locally"}
    assert result["unknowns"] == [
        "envFrom imports source keys without explicit per-key references; exact environment keys are not indexed.",
        "An envFrom source without a local declaration may be managed by another repository, chart, controller, or deployment process.",
    ]


def test_describe_runtime_configuration_marks_an_optional_env_from_source(tmp_path):
    conn = open_db(tmp_path / "runtime-configuration-optional-env-from.db")
    repository_id = repositories.ensure_repository(conn, "shop", "/repos/shop")
    services.ensure_service(conn, "orders", "/repos/shop/orders", "node-ts", repository_id=repository_id)
    kubernetes_configuration.replace_kubernetes_configuration_source_imports(conn, repository_id, [
        KubernetesConfigurationSourceImport(
            source_kind="config_map", source_name="optional-defaults", prefix=None, optional=True,
            workload_kind="Deployment", workload_name="orders", container_name="api",
            file_path="deploy/orders.yaml", start_line=11, end_line=14, matched_service_name="orders",
        ),
        KubernetesConfigurationSourceImport(
            source_kind="secret", source_name="required-secrets", prefix=None, optional=False,
            workload_kind="Deployment", workload_name="orders", container_name="api",
            file_path="deploy/orders.yaml", start_line=15, end_line=17, matched_service_name="orders",
        ),
    ])

    result = queries.describe_runtime_configuration(conn, "orders")

    assert [source["availability"] for source in result["source_imports"]] == ["optional", "required"]


def test_describe_runtime_configuration_identifies_an_init_container_source_import(tmp_path):
    conn = open_db(tmp_path / "runtime-configuration-init-container-env-from.db")
    repository_id = repositories.ensure_repository(conn, "shop", "/repos/shop")
    services.ensure_service(conn, "orders", "/repos/shop/orders", "node-ts", repository_id=repository_id)
    kubernetes_configuration.replace_kubernetes_configuration_source_imports(conn, repository_id, [
        KubernetesConfigurationSourceImport(
            source_kind="secret", source_name="migration-secrets", prefix=None, container_role="initialization",
            workload_kind="Deployment", workload_name="orders", container_name="migrate",
            file_path="deploy/orders.yaml", start_line=11, end_line=14, matched_service_name="orders",
        ),
    ])

    result = queries.describe_runtime_configuration(conn, "orders")

    assert result["source_imports"][0]["workload"] == {
        "kind": "Deployment", "name": "orders", "container": "migrate", "container_role": "initialization",
    }


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
