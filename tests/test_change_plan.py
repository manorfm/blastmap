"""Contract tests for the bounded, evidence-first change planning entrypoint."""
import json

from jsonschema import validate

from orbitkb.analysis.models import (
    AnalysisResult,
    ConfigurationBinding,
    EntryPoint,
    ErrorContract,
    Evidence,
    FeatureFlag,
    MigrationFact,
    StaticServiceCall,
)
from orbitkb.db.connection import open_db
from orbitkb.db.repositories import change_plans
from orbitkb.db.repositories import flows as flows_repo
from orbitkb.db.repositories import (
    kubernetes_configuration as kubernetes_configuration_repo,
)
from orbitkb.db.repositories import messages as messages_repo
from orbitkb.db.repositories import persistence as persistence_repo
from orbitkb.db.repositories import repositories as repositories_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.generation.architecture import recompute_architecture_view
from orbitkb.generation.change_plan import (
    derive_error_mapping_review_units,
    derive_partial_write_resilience_review_units,
    derive_persistence_migration_review_units,
    derive_public_object_storage_review_units,
    derive_read_entrypoint_side_effect_review_units,
    derive_retry_delivery_review_units,
    derive_retry_downstream_error_review_units,
    derive_retry_policy_review_units,
    derive_retry_write_publish_review_units,
    derive_runtime_configuration_review_units,
    derive_runtime_configuration_source_import_unknown_review_units,
    derive_timeout_fallback_review_units,
)
from orbitkb.generation.llm_harness import load_schema
from orbitkb.generation.token_budget import TokenMeasurement
from orbitkb.iac.models import (
    KubernetesConfigurationBinding,
    KubernetesConfigurationKeyMismatch,
    KubernetesConfigurationSourceImportUnknown,
    KubernetesConfigurationSourceUnknown,
)
from orbitkb.mcp import queries
from tests.test_change_surface import FakeBackend, _build_pix_fixture


def test_plan_change_wraps_the_indexed_surface_in_a_stable_initial_contract(tmp_path):
    conn = _build_pix_fixture(tmp_path / "plan.db")
    backend = FakeBackend({
        "primary": [{"service": "checkout-service", "reason": "owns checkout", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    })

    result = queries.plan_change(conn, backend, "Add a payment method", token_budget=2200)

    assert result["plan_id"].startswith("cp_")
    assert result["status"] == "ready"
    assert result["surface"]["primary"] == [{
        "service": "checkout-service", "reason": "owns checkout", "confidence": 0.9,
        "evidence": [{"file": "checkout.py", "start_line": 1, "end_line": 20}],
    }]
    assert result["decision_points"] == []
    assert result["change_units"] == []
    assert result["budget"]["requested_tokens"] == 2200
    assert result["budget"]["estimated_tokens"] > 0
    assert result["budget"]["measurement"] == "byte_estimate"
    assert result["budget"]["truncated"] is False
    validate(result, load_schema("plan_change"))

    plan = change_plans.get_plan(conn, int(result["plan_id"].removeprefix("cp_")))
    assert plan["change_surface_run_id"] is not None
    assert plan["status"] == "ready"
    assert plan["requested_tokens"] == 2200
    assert plan["estimated_tokens"] == result["budget"]["estimated_tokens"]
    assert plan["token_measurement"] == result["budget"]["measurement"]
    assert plan["truncated"] == 0


def test_plan_change_persists_the_tokenizer_measurement_when_available(tmp_path, monkeypatch):
    conn = _build_pix_fixture(tmp_path / "tokenizer-plan.db")
    backend = FakeBackend({"primary": [{
        "service": "checkout-service", "reason": "owns checkout", "confidence": 0.9,
        "evidence": [{"file": "checkout.py", "start_line": 1, "end_line": 20}],
    }], "secondary": [], "no_change": []})
    def measure_response(response):
        tokens = 41 if response["budget"]["measurement"] == "tiktoken:o200k_base" else 40
        return TokenMeasurement(tokens, "tiktoken:o200k_base")

    monkeypatch.setattr(queries, "measure_json_tokens", measure_response)

    result = queries.plan_change(conn, backend, "Add a payment method")

    assert result["budget"] == {
        "requested_tokens": 2200, "estimated_tokens": 41,
        "measurement": "tiktoken:o200k_base", "truncated": False,
    }
    plan = change_plans.get_plan(conn, int(result["plan_id"].removeprefix("cp_")))
    assert plan["token_measurement"] == "tiktoken:o200k_base"


def test_plan_change_rejects_an_invalid_token_budget_without_calling_the_backend(tmp_path):
    conn = _build_pix_fixture(tmp_path / "invalid-budget.db")
    backend = FakeBackend({})

    assert queries.plan_change(conn, backend, "Add a payment method", token_budget=0) == {
        "error": "token_budget must be between 1 and 2200 (got 0)",
    }
    assert backend.calls == 0


def test_plan_change_derives_an_http_contract_review_unit_for_a_resolved_static_endpoint(tmp_path):
    conn = _build_pix_fixture(tmp_path / "http-unit.db")
    checkout = services_repo.get_service_by_name(conn, "checkout-service")
    payments = services_repo.get_service_by_name(conn, "payments-service")
    evidence = Evidence("CheckoutService.java", 18, 18)
    flows_repo.replace_analysis(conn, checkout["id"], AnalysisResult(static_service_calls=[
        StaticServiceCall(
            source="CheckoutService.submit", target_service="payments-service", protocol="http",
            target_method="POST", target_path="/authorizations", evidence=evidence,
        ),
    ]))
    flows_repo.replace_analysis(conn, payments["id"], AnalysisResult(entrypoints=[
        EntryPoint("http", "POST", "/authorizations", "PaymentsController.authorize", evidence),
    ]))
    backend = FakeBackend({
        "primary": [{"service": "checkout-service", "reason": "owns checkout", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    })

    result = queries.plan_change(conn, backend, "Add a payment method")

    assert result["status"] == "ready"
    assert result["change_units"] == [{
        "id": "http-contract:checkout-service:payments-service:POST:/authorizations",
        "service": "checkout-service",
        "target": {
            "role": "integration",
            "symbol": "CheckoutService.submit",
            "evidence": [{"file": "CheckoutService.java", "start_line": 18, "end_line": 18}],
        },
        "action": "review",
        "reason": "a source-proven HTTP call reaches payments-service POST /authorizations; review both sides if this boundary changes.",
        "preconditions": [],
        "related_contracts": ["POST /authorizations"],
        "dependencies": ["payments-service"],
        "validation": ["verify client and payments-service agree on POST /authorizations"],
        "confidence": 1.0,
        "evidence": [{"file": "CheckoutService.java", "start_line": 18, "end_line": 18}],
    }]


def test_plan_change_derives_an_error_mapping_review_unit_for_a_proven_4xx_to_5xx_degradation(tmp_path):
    conn = _build_pix_fixture(tmp_path / "error-unit.db")
    payments = services_repo.get_service_by_name(conn, "payments-service")
    messages_repo.replace_messages(conn, payments["id"], [], [])
    origin_evidence = Evidence("PaymentService.java", 24, 24)
    mapping_evidence = Evidence("ApiExceptionHandler.java", 42, 42)
    flows_repo.replace_analysis(conn, payments["id"], AnalysisResult(error_contracts=[
        ErrorContract(
            source="PaymentService.authorize", role="raises", error_kind="conflict",
            internal_type="PaymentConflict", protocol="internal", transport_code=None,
            public_code=None, exposes_internal_detail=False, retryability="not_retryable",
            evidence=origin_evidence,
        ),
        ErrorContract(
            source="ApiExceptionHandler.handlePaymentConflict", role="maps", error_kind="unexpected",
            internal_type="PaymentConflict", protocol="http", transport_code="500",
            public_code=None, exposes_internal_detail=False, retryability="unknown",
            evidence=mapping_evidence,
        ),
    ]))
    recompute_architecture_view(conn)

    result = queries.plan_change(conn, FakeBackend({
        "primary": [{"service": "payments-service", "reason": "owns authorization", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    }), "Preserve payment conflict semantics")

    assert result["status"] == "ready"
    assert result["change_units"] == [{
        "id": "error-mapping:payments-service:PaymentConflict:ApiExceptionHandler.handlePaymentConflict:500",
        "service": "payments-service",
        "target": {
            "role": "error_mapping",
            "symbol": "ApiExceptionHandler.handlePaymentConflict",
            "evidence": [
                {"file": "PaymentService.java", "start_line": 24, "end_line": 24},
                {"file": "ApiExceptionHandler.java", "start_line": 42, "end_line": 42},
            ],
        },
        "action": "review",
        "reason": (
            "PaymentService.authorize raises conflict error 'PaymentConflict', but "
            "ApiExceptionHandler.handlePaymentConflict maps the same type to HTTP 500."
        ),
        "preconditions": [],
        "related_contracts": ["error:PaymentConflict", "HTTP 500"],
        "dependencies": [],
        "validation": [
            "verify PaymentConflict preserves a documented client-error response or document the HTTP 500 translation",
        ],
        "confidence": 0.85,
        "evidence": [
            {"file": "PaymentService.java", "start_line": 24, "end_line": 24},
            {"file": "ApiExceptionHandler.java", "start_line": 42, "end_line": 42},
        ],
    }]
    detail = queries.describe_change_unit(
        conn,
        result["plan_id"],
        "error-mapping:payments-service:PaymentConflict:ApiExceptionHandler.handlePaymentConflict:500",
    )
    assert detail["minimal_reading"] == [{
        "service": "payments-service",
        "purpose": "identify the entrypoint that owns the public error contract",
        "recommended_query": {"tool": "list_entrypoints", "arguments": {"service": "payments-service"}},
    }]
    validate(detail, load_schema("describe_change_unit"))


def test_plan_change_derives_a_migration_review_unit_for_an_affected_persisted_table(tmp_path):
    conn = _build_pix_fixture(tmp_path / "migration-unit.db")
    payments = services_repo.get_service_by_name(conn, "payments-service")
    messages_repo.replace_messages(conn, payments["id"], [], [])
    persistence_repo.replace_persistence_entities(
        conn,
        payments["id"],
        [{"name": "payment_method", "kind": "sql_table", "schema_json": []}],
        [{"file": "payments/models.py", "start_line": 8, "end_line": 12}],
    )
    flows_repo.replace_analysis(conn, payments["id"], AnalysisResult(migration_facts=[
        MigrationFact(
            "drop_column", "payment_method", "legacy_token", True,
            Evidence("db/migration/V12__payment_method.sql", 5, 5),
        ),
    ]))

    result = queries.plan_change(conn, FakeBackend({
        "primary": [{"service": "payments-service", "reason": "owns payment method", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    }), "Add a payment method")

    assert result["status"] == "ready"
    assert result["change_units"] == [{
        "id": "persistence-migration:payments-service:payment_method",
        "service": "payments-service",
        "target": {
            "role": "persistence",
            "symbol": "table:payment_method",
            "evidence": [
                {"file": "payments/models.py", "start_line": 8, "end_line": 12},
                {"file": "db/migration/V12__payment_method.sql", "start_line": 5, "end_line": 5},
            ],
        },
        "action": "review",
        "reason": "payment_method is on the indexed change surface and has 1 source-proven migration operation; review schema compatibility before altering it.",
        "preconditions": [],
        "related_contracts": ["database:payment_method"],
        "dependencies": [],
        "validation": [
            "review payment_method schema and its 1 indexed migration operation before altering persistence",
            "verify deployment order, backup, and rollback for destructive migration operations",
        ],
        "confidence": 1.0,
        "evidence": [
            {"file": "payments/models.py", "start_line": 8, "end_line": 12},
            {"file": "db/migration/V12__payment_method.sql", "start_line": 5, "end_line": 5},
        ],
    }]
    detail = queries.describe_change_unit(
        conn, result["plan_id"], "persistence-migration:payments-service:payment_method",
    )
    assert detail["minimal_reading"] == [{
        "service": "payments-service",
        "purpose": "confirm the affected schema and indexed migration operations",
        "recommended_query": {"tool": "describe_persistence", "arguments": {"service": "payments-service"}},
    }]
    validate(detail, load_schema("describe_change_unit"))
    refined = queries.refine_change_plan(conn, result["plan_id"], [])
    assert refined["change_units"] == result["change_units"]
    validate(refined, load_schema("refine_change_plan"))


def test_plan_change_derives_one_feature_flag_review_unit_for_a_primary_service(tmp_path):
    conn = _build_pix_fixture(tmp_path / "feature-flag-unit.db")
    checkout = services_repo.get_service_by_name(conn, "checkout-service")
    flows_repo.replace_analysis(conn, checkout["id"], AnalysisResult(feature_flags=[
        FeatureFlag(
            source="CheckoutService.submit", key="checkout.new-payment-flow", provider="launchdarkly",
            evidence=Evidence("src/checkout.ts", 18, 18),
        ),
        FeatureFlag(
            source="CheckoutService.preview", key="checkout.new-payment-flow", provider="launchdarkly",
            evidence=Evidence("src/checkout.ts", 31, 31),
        ),
    ]))

    result = queries.plan_change(conn, FakeBackend({
        "primary": [{"service": "checkout-service", "reason": "owns checkout", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    }), "Change the checkout payment flow")

    assert result["status"] == "ready"
    assert result["change_units"] == [{
        "id": "feature-flag:checkout-service:launchdarkly:checkout.new-payment-flow",
        "service": "checkout-service",
        "target": {
            "role": "feature_flag",
            "symbol": "launchdarkly:checkout.new-payment-flow",
            "evidence": [
                {"file": "src/checkout.ts", "start_line": 18, "end_line": 18},
                {"file": "src/checkout.ts", "start_line": 31, "end_line": 31},
            ],
        },
        "action": "review",
        "reason": (
            "checkout.new-payment-flow is read through launchdarkly at 2 indexed locations; "
            "review targeting, default behavior, and rollout if the guarded behavior changes."
        ),
        "preconditions": [],
        "related_contracts": ["feature_flag:launchdarkly:checkout.new-payment-flow"],
        "dependencies": [],
        "validation": [
            "verify checkout.new-payment-flow targeting, default behavior, and rollout state match the requested change",
            "verify guarded code remains safe when checkout.new-payment-flow is disabled",
        ],
        "confidence": 1.0,
        "evidence": [
            {"file": "src/checkout.ts", "start_line": 18, "end_line": 18},
            {"file": "src/checkout.ts", "start_line": 31, "end_line": 31},
        ],
    }]
    detail = queries.describe_change_unit(
        conn, result["plan_id"], "feature-flag:checkout-service:launchdarkly:checkout.new-payment-flow",
    )
    assert detail["minimal_reading"] == [{
        "service": "checkout-service",
        "purpose": "confirm the indexed feature flag and its guarded behavior",
        "recommended_query": {"tool": "describe_feature_flags", "arguments": {"service": "checkout-service"}},
    }]
    validate(detail, load_schema("describe_change_unit"))
    refined = queries.refine_change_plan(conn, result["plan_id"], [])
    validate(refined, load_schema("refine_change_plan"))


def test_plan_change_derives_a_configuration_review_unit_for_an_exact_runtime_binding(tmp_path):
    conn = _build_pix_fixture(tmp_path / "runtime-configuration-unit.db")
    checkout = services_repo.get_service_by_name(conn, "checkout-service")
    repository_id = repositories_repo.ensure_repository(conn, "shop", "/tmp/shop")
    conn.execute("UPDATE services SET repository_id = ? WHERE id = ?", (repository_id, checkout["id"]))
    conn.commit()
    flows_repo.replace_analysis(conn, checkout["id"], AnalysisResult(configuration_bindings=[
        ConfigurationBinding(
            "CheckoutService.submit", "ORDERS_TOPIC", "environment", False,
            Evidence("checkout.py", 18, 18),
        ),
    ]))
    kubernetes_configuration_repo.replace_kubernetes_configuration_bindings(conn, repository_id, [
        KubernetesConfigurationBinding(
            environment_key="ORDERS_TOPIC", source_kind="config_map", source_name="orders-config",
            source_key="orders-topic", workload_kind="Deployment", workload_name="checkout",
            container_name="api", file_path="deploy/checkout.yaml", start_line=12, end_line=17,
            matched_service_name="checkout-service",
        ),
    ])

    result = queries.plan_change(conn, FakeBackend({
        "primary": [{"service": "checkout-service", "reason": "owns checkout", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    }), "Change checkout messaging configuration", repository="shop")

    assert result["status"] == "ready"
    assert result["change_units"] == [{
        "id": "runtime-configuration:checkout-service:ORDERS_TOPIC",
        "service": "checkout-service",
        "target": {
            "role": "configuration",
            "symbol": "environment:ORDERS_TOPIC",
            "evidence": [
                {"file": "checkout.py", "start_line": 18, "end_line": 18},
                {"file": "deploy/checkout.yaml", "start_line": 12, "end_line": 17},
            ],
        },
        "action": "review",
        "reason": (
            "ORDERS_TOPIC is read at 1 local location and bound to 1 indexed Kubernetes workload; "
            "review both layers if its behavior changes."
        ),
        "preconditions": [],
        "related_contracts": [
            "configuration:environment:ORDERS_TOPIC",
            "configuration:config_map:orders-config:orders-topic",
        ],
        "dependencies": [],
        "validation": [
            "verify ORDERS_TOPIC remains compatible with its indexed ConfigMap or Secret source",
            "verify indexed Kubernetes workload references remain valid during rollout",
        ],
        "confidence": 1.0,
        "evidence": [
            {"file": "checkout.py", "start_line": 18, "end_line": 18},
            {"file": "deploy/checkout.yaml", "start_line": 12, "end_line": 17},
        ],
    }]
    detail = queries.describe_change_unit(
        conn, result["plan_id"], "runtime-configuration:checkout-service:ORDERS_TOPIC",
    )
    assert detail["minimal_reading"] == [{
        "service": "checkout-service",
        "purpose": "confirm the indexed code and Kubernetes configuration binding",
        "recommended_query": {"tool": "describe_configuration", "arguments": {"service": "checkout-service"}},
    }]
    validate(detail, load_schema("describe_change_unit"))
    refined = queries.refine_change_plan(conn, result["plan_id"], [])
    validate(refined, load_schema("refine_change_plan"))


def test_plan_change_derives_a_review_for_a_persisted_kubernetes_configuration_mismatch(tmp_path):
    conn = _build_pix_fixture(tmp_path / "runtime-configuration-mismatch-unit.db")
    checkout = services_repo.get_service_by_name(conn, "checkout-service")
    repository_id = repositories_repo.ensure_repository(conn, "shop", "/tmp/shop")
    conn.execute("UPDATE services SET repository_id = ? WHERE id = ?", (repository_id, checkout["id"]))
    conn.commit()
    kubernetes_configuration_repo.replace_kubernetes_configuration_key_mismatches(conn, repository_id, [
        KubernetesConfigurationKeyMismatch(
            environment_key="ORDERS_TOPIC", source_kind="config_map", source_name="orders-config",
            source_key="orders-topic", reference_file_path="deploy/checkout.yaml", reference_start_line=12,
            reference_end_line=17, declaration_file_path="deploy/config.yaml", declaration_start_line=1,
            declaration_end_line=7, matched_service_name="checkout-service",
        ),
    ])

    result = queries.plan_change(conn, FakeBackend({
        "primary": [{"service": "checkout-service", "reason": "owns checkout", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    }), "Change checkout configuration", repository="shop")

    assert result["change_units"] == [{
        "id": "runtime-configuration-mismatch:checkout-service:config_map:orders-config:orders-topic:ORDERS_TOPIC",
        "service": "checkout-service",
        "target": {
            "role": "configuration",
            "symbol": "kubernetes:config_map:orders-config:orders-topic",
            "evidence": [
                {"file": "deploy/checkout.yaml", "start_line": 12, "end_line": 17},
                {"file": "deploy/config.yaml", "start_line": 1, "end_line": 7},
            ],
        },
        "action": "review",
        "reason": (
            "ORDERS_TOPIC references ConfigMap orders-config key orders-topic, but its single indexed declaration "
            "does not list that key."
        ),
        "preconditions": [],
        "related_contracts": [
            "configuration:environment:ORDERS_TOPIC",
            "configuration:config_map:orders-config:orders-topic",
        ],
        "dependencies": [],
        "validation": [
            "verify the ConfigMap declaration or workload reference is corrected before rollout",
            "verify behavior remains safe when ORDERS_TOPIC is unavailable",
        ],
        "confidence": 0.9,
        "evidence": [
            {"file": "deploy/checkout.yaml", "start_line": 12, "end_line": 17},
            {"file": "deploy/config.yaml", "start_line": 1, "end_line": 7},
        ],
    }]
    detail = queries.describe_change_unit(
        conn,
        result["plan_id"],
        "runtime-configuration-mismatch:checkout-service:config_map:orders-config:orders-topic:ORDERS_TOPIC",
    )
    assert detail["minimal_reading"] == [{
        "service": "checkout-service",
        "purpose": "confirm the indexed Kubernetes configuration mismatch",
        "recommended_query": {"tool": "describe_runtime_configuration", "arguments": {"service": "checkout-service"}},
    }]
    assert "evidence_follow_up" not in detail
    validate(detail, load_schema("describe_change_unit"))


def test_plan_change_derives_a_review_for_an_unresolved_kubernetes_configuration_source(tmp_path):
    conn = _build_pix_fixture(tmp_path / "runtime-configuration-source-unknown-unit.db")
    checkout = services_repo.get_service_by_name(conn, "checkout-service")
    repository_id = repositories_repo.ensure_repository(conn, "shop", "/tmp/shop")
    conn.execute("UPDATE services SET repository_id = ? WHERE id = ?", (repository_id, checkout["id"]))
    conn.commit()
    kubernetes_configuration_repo.replace_kubernetes_configuration_source_unknowns(conn, repository_id, [
        KubernetesConfigurationSourceUnknown(
            environment_key="ORDERS_TOPIC", source_kind="config_map", source_name="external-config",
            source_key="orders-topic", reference_file_path="deploy/checkout.yaml", reference_start_line=12,
            reference_end_line=17, matched_service_name="checkout-service",
        ),
    ])

    result = queries.plan_change(conn, FakeBackend({
        "primary": [{"service": "checkout-service", "reason": "owns checkout", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    }), "Change checkout configuration", repository="shop")

    assert result["change_units"] == [{
        "id": "runtime-configuration-source-unknown:checkout-service:config_map:external-config:orders-topic:ORDERS_TOPIC",
        "service": "checkout-service",
        "target": {
            "role": "configuration",
            "symbol": "kubernetes:config_map:external-config:orders-topic",
            "evidence": [{"file": "deploy/checkout.yaml", "start_line": 12, "end_line": 17}],
        },
        "action": "review",
        "reason": (
            "ORDERS_TOPIC references ConfigMap external-config key orders-topic, but no matching local declaration "
            "was indexed."
        ),
        "preconditions": [],
        "related_contracts": [
            "configuration:environment:ORDERS_TOPIC",
            "configuration:config_map:external-config:orders-topic",
        ],
        "dependencies": [],
        "validation": [
            "confirm the owning repository, chart, controller, or deployment process for ConfigMap external-config",
            "verify ORDERS_TOPIC remains available and compatible throughout rollout",
        ],
        "confidence": 0.4,
        "evidence": [{"file": "deploy/checkout.yaml", "start_line": 12, "end_line": 17}],
    }]
    detail = queries.describe_change_unit(
        conn,
        result["plan_id"],
        "runtime-configuration-source-unknown:checkout-service:config_map:external-config:orders-topic:ORDERS_TOPIC",
    )
    assert detail["minimal_reading"] == [{
        "service": "checkout-service",
        "purpose": "confirm the owner of the unresolved Kubernetes configuration source",
        "recommended_query": {"tool": "describe_runtime_configuration", "arguments": {"service": "checkout-service"}},
    }]
    validate(detail, load_schema("describe_change_unit"))


def test_plan_change_derives_a_review_for_an_unresolved_kubernetes_env_from_source(tmp_path):
    conn = _build_pix_fixture(tmp_path / "runtime-configuration-source-import-unknown-unit.db")
    checkout = services_repo.get_service_by_name(conn, "checkout-service")
    repository_id = repositories_repo.ensure_repository(conn, "shop", "/tmp/shop")
    conn.execute("UPDATE services SET repository_id = ? WHERE id = ?", (repository_id, checkout["id"]))
    conn.commit()
    kubernetes_configuration_repo.replace_kubernetes_configuration_source_import_unknowns(conn, repository_id, [
        KubernetesConfigurationSourceImportUnknown(
            source_kind="config_map", source_name="external-config", prefix="ORDERS_",
            reference_file_path="deploy/checkout.yaml", reference_start_line=12, reference_end_line=15,
            workload_kind="Deployment", workload_name="checkout", container_name="migrate",
            container_role="initialization", optional=True, matched_service_name="checkout-service",
        ),
        KubernetesConfigurationSourceImportUnknown(
            source_kind="config_map", source_name="external-config", prefix="PAYMENTS_",
            reference_file_path="deploy/checkout.yaml", reference_start_line=20, reference_end_line=23,
            workload_kind="Deployment", workload_name="checkout", container_name="api",
            container_role="application", optional=True, matched_service_name="checkout-service",
        ),
    ])

    result = queries.plan_change(conn, FakeBackend({
        "primary": [{"service": "checkout-service", "reason": "owns checkout", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    }), "Change checkout configuration", repository="shop")

    assert result["change_units"] == [{
        "id": "runtime-configuration-source-import-unknown:checkout-service:config_map:external-config",
        "service": "checkout-service",
        "target": {
            "role": "configuration",
            "symbol": "kubernetes:config_map:external-config",
            "evidence": [
                {"file": "deploy/checkout.yaml", "start_line": 12, "end_line": 15},
                {"file": "deploy/checkout.yaml", "start_line": 20, "end_line": 23},
            ],
            "workloads": [
                {
                    "kind": "Deployment", "name": "checkout", "container": "migrate",
                    "container_role": "initialization", "prefixes": ["ORDERS_"],
                    "evidence": [{"file": "deploy/checkout.yaml", "start_line": 12, "end_line": 15}],
                },
                {
                    "kind": "Deployment", "name": "checkout", "container": "api",
                    "container_role": "application", "prefixes": ["PAYMENTS_"],
                    "evidence": [{"file": "deploy/checkout.yaml", "start_line": 20, "end_line": 23}],
                },
            ],
        },
        "action": "review",
        "reason": (
            "checkout-service imports keys from ConfigMap external-config through envFrom, but no matching local "
            "declaration was indexed."
        ),
        "preconditions": [],
        "related_contracts": ["configuration:config_map:external-config"],
        "dependencies": [],
        "validation": [
            "confirm the owning repository, chart, controller, or deployment process for ConfigMap external-config",
            "verify requested behavior remains safe when the optional ConfigMap external-config is unavailable",
            "verify initialization completes before application containers start",
        ],
        "confidence": 0.4,
        "evidence": [
            {"file": "deploy/checkout.yaml", "start_line": 12, "end_line": 15},
            {"file": "deploy/checkout.yaml", "start_line": 20, "end_line": 23},
        ],
    }]
    detail = queries.describe_change_unit(
        conn,
        result["plan_id"],
        "runtime-configuration-source-import-unknown:checkout-service:config_map:external-config",
    )
    assert detail["minimal_reading"] == [{
        "service": "checkout-service",
        "purpose": (
            "confirm the owner of the unresolved Kubernetes configuration source and inspect "
            "Deployment checkout container migrate (deploy/checkout.yaml:12-15) and Deployment checkout container api "
            "(deploy/checkout.yaml:20-23)"
        ),
        "recommended_query": {"tool": "describe_runtime_configuration", "arguments": {"service": "checkout-service"}},
    }]
    assert "evidence_follow_up" not in detail
    validate(detail, load_schema("describe_change_unit"))


def test_env_from_source_import_review_requires_availability_confirmation_when_unknown():
    units = derive_runtime_configuration_source_import_unknown_review_units({
        "checkout-service": [{
            "source_kind": "config_map", "source_name": "external-config", "prefix": None,
            "optional": None, "reference_file_path": "deploy/checkout.yaml", "reference_start_line": 12,
            "reference_end_line": 15,
        }],
    }, {"checkout-service"})

    assert units[0]["validation"] == [
        "confirm the owning repository, chart, controller, or deployment process for ConfigMap external-config",
        "confirm source availability before relying on imported configuration during rollout",
    ]
    assert "workloads" not in units[0]["target"]
    assert queries._minimal_unit_reading(units[0]) == [{
        "service": "checkout-service",
        "purpose": "confirm the owner of the unresolved Kubernetes configuration source",
        "recommended_query": {"tool": "describe_runtime_configuration", "arguments": {"service": "checkout-service"}},
    }]


def test_workload_evidence_locations_are_bounded_and_deduplicated():
    assert queries._workload_evidence_locations([
        {"file": "deploy/orders.yaml", "start_line": 12, "end_line": 15},
        {"file": "deploy/orders.yaml", "start_line": 12, "end_line": 15},
        {"file": "deploy/orders.yaml", "start_line": 20, "end_line": 23},
        {"file": "deploy/orders.yaml", "start_line": 28, "end_line": 30},
        {"file": "deploy/orders.yaml", "start_line": "invalid", "end_line": 31},
    ]) == ["deploy/orders.yaml:12-15", "deploy/orders.yaml:20-23"]


def test_env_from_source_import_review_caps_structured_workload_evidence():
    units = derive_runtime_configuration_source_import_unknown_review_units({
        "checkout-service": [
            {
                "source_kind": "config_map", "source_name": "external-config", "prefix": None,
                "workload_kind": "Deployment", "workload_name": "checkout", "container_name": "api",
                "reference_file_path": "deploy/checkout.yaml", "reference_start_line": 12, "reference_end_line": 15,
            },
            {
                "source_kind": "config_map", "source_name": "external-config", "prefix": None,
                "workload_kind": "Deployment", "workload_name": "checkout", "container_name": "api",
                "reference_file_path": "deploy/checkout.yaml", "reference_start_line": 20, "reference_end_line": 23,
            },
            {
                "source_kind": "config_map", "source_name": "external-config", "prefix": None,
                "workload_kind": "Deployment", "workload_name": "checkout", "container_name": "api",
                "reference_file_path": "deploy/checkout.yaml", "reference_start_line": 28, "reference_end_line": 31,
            },
        ],
    }, {"checkout-service"})

    assert units[0]["target"]["workloads"] == [{
        "kind": "Deployment", "name": "checkout", "container": "api", "includes_unprefixed_import": True,
        "evidence": [
            {"file": "deploy/checkout.yaml", "start_line": 12, "end_line": 15},
            {"file": "deploy/checkout.yaml", "start_line": 20, "end_line": 23},
        ],
        "evidence_truncated": True,
        "evidence_total": 3,
    }]


def test_describe_change_unit_adds_follow_up_for_truncated_workload_evidence(tmp_path):
    conn = _build_pix_fixture(tmp_path / "runtime-configuration-source-import-evidence-follow-up.db")
    checkout = services_repo.get_service_by_name(conn, "checkout-service")
    repository_id = repositories_repo.ensure_repository(conn, "shop", "/tmp/shop")
    conn.execute("UPDATE services SET repository_id = ? WHERE id = ?", (repository_id, checkout["id"]))
    conn.commit()
    kubernetes_configuration_repo.replace_kubernetes_configuration_source_import_unknowns(conn, repository_id, [
        KubernetesConfigurationSourceImportUnknown(
            source_kind="config_map", source_name="external-config", prefix=None,
            reference_file_path="deploy/checkout.yaml", reference_start_line=12, reference_end_line=15,
            workload_kind="Deployment", workload_name="checkout", container_name="api",
            matched_service_name="checkout-service",
        ),
        KubernetesConfigurationSourceImportUnknown(
            source_kind="config_map", source_name="external-config", prefix=None,
            reference_file_path="deploy/checkout.yaml", reference_start_line=20, reference_end_line=23,
            workload_kind="Deployment", workload_name="checkout", container_name="api",
            matched_service_name="checkout-service",
        ),
        KubernetesConfigurationSourceImportUnknown(
            source_kind="config_map", source_name="external-config", prefix=None,
            reference_file_path="deploy/checkout.yaml", reference_start_line=28, reference_end_line=31,
            workload_kind="Deployment", workload_name="checkout", container_name="api",
            matched_service_name="checkout-service",
        ),
    ])

    plan = queries.plan_change(conn, FakeBackend({
        "primary": [{"service": "checkout-service", "reason": "owns checkout", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    }), "Change checkout configuration", repository="shop")
    detail = queries.describe_change_unit(
        conn,
        plan["plan_id"],
        "runtime-configuration-source-import-unknown:checkout-service:config_map:external-config",
    )

    assert detail["evidence_follow_up"] == {
        "reason": "workload evidence is truncated",
        "recommended_next_step": "inspect_change_unit_evidence",
        "workloads": [{
            "kind": "Deployment", "name": "checkout", "container": "api", "evidence_total": 3,
        }],
        "recommended_query": {
            "tool": "describe_runtime_configuration", "arguments": {"service": "checkout-service"},
        },
    }
    validate(detail, load_schema("describe_change_unit"))
    completed_follow_up = queries.validate_runtime_configuration_follow_up(
        conn,
        plan["plan_id"],
        "runtime-configuration-source-import-unknown:checkout-service:config_map:external-config",
        [{"kind": "Deployment", "name": "checkout", "container": "api"}],
        source_import_truncated=True,
    )
    assert len(completed_follow_up["trace_id"]) == 22
    assert "plan_id" not in completed_follow_up
    assert "change_unit_id" not in completed_follow_up
    assert {key: value for key, value in completed_follow_up.items() if key != "trace_id"} == {
        "status": "complete",
        "matched_workload_count": 1,
        "missing_workloads": [],
        "continue_pagination": False,
    }
    assert queries.validate_runtime_configuration_follow_up(
        conn,
        plan["plan_id"],
        "runtime-configuration-source-import-unknown:checkout-service:config_map:external-config",
        [{"kind": "Deployment", "name": "checkout", "container": "api"}],
        source_import_truncated=True,
        include_trace_details=True,
    )["change_unit_id"] == "runtime-configuration-source-import-unknown:checkout-service:config_map:external-config"
    assert queries.validate_runtime_configuration_follow_up(
        conn,
        plan["plan_id"],
        "runtime-configuration-source-import-unknown:checkout-service:config_map:external-config",
        [{"kind": "Deployment", "name": "checkout", "container": "api"}],
        source_import_truncated=True,
        include_matched_workloads=True,
    )["matched_workloads"] == [{
        "kind": "Deployment", "name": "checkout", "container": "api", "evidence_total": 3,
    }]
    assert queries.validate_runtime_configuration_follow_up(
        conn,
        plan["plan_id"],
        "runtime-configuration-source-import-unknown:checkout-service:config_map:external-config",
        [{"workload": {"kind": "Deployment", "name": "checkout", "container": "api"}}],
        source_import_truncated=True,
    )["status"] == "complete"
    assert queries.validate_runtime_configuration_follow_up(
        conn,
        plan["plan_id"],
        "runtime-configuration-source-import-unknown:checkout-service:config_map:external-config",
        [{
            "kind": "Deployment", "name": "checkout", "container": "api",
            "workload": {"kind": "StatefulSet", "name": "checkout", "container": "api"},
        }],
        source_import_truncated=True,
    ) == {"error": "returned workload identity conflicts with nested workload"}
    first_follow_up = queries.validate_runtime_configuration_follow_up(
        conn,
        plan["plan_id"],
        "runtime-configuration-source-import-unknown:checkout-service:config_map:external-config",
        [],
        source_import_truncated=True,
    )
    assert len(first_follow_up["page_fingerprint"]) == 43
    assert first_follow_up["page_fingerprint"].replace("-", "").replace("_", "").isalnum()
    assert {key: value for key, value in first_follow_up.items() if key not in {"page_fingerprint", "trace_id"}} == {
        "status": "needs_next_page",
        "matched_workload_count": 0,
        "missing_workloads": [{
            "kind": "Deployment", "name": "checkout", "container": "api", "evidence_total": 3,
        }],
        "continue_pagination": True,
        "next_query": {
            "tool": "describe_runtime_configuration",
            "arguments": {
                "service": "checkout-service", "limit": 50, "offset": 0,
                "workloads": [{"kind": "Deployment", "name": "checkout", "container": "api"}],
            },
        },
    }
    stalled_follow_up = queries.validate_runtime_configuration_follow_up(
        conn,
        plan["plan_id"],
        "runtime-configuration-source-import-unknown:checkout-service:config_map:external-config",
        [],
        source_import_truncated=True,
        current_offset=50,
        previous_page_fingerprint=first_follow_up["page_fingerprint"],
    )
    assert stalled_follow_up["trace_id"] == first_follow_up["trace_id"]
    assert {key: value for key, value in stalled_follow_up.items() if key != "trace_id"} == {
        "status": "stalled",
        "matched_workload_count": 0,
        "missing_workloads": [{
            "kind": "Deployment", "name": "checkout", "container": "api", "evidence_total": 3,
        }],
        "continue_pagination": False,
        "reason": "runtime configuration page repeated",
    }
    assert queries.validate_runtime_configuration_follow_up(
        conn,
        plan["plan_id"],
        "runtime-configuration-source-import-unknown:checkout-service:config_map:external-config",
        [],
        source_import_truncated=True,
        previous_page_fingerprint="invalid",
    ) == {"error": "previous_page_fingerprint must be a URL-safe SHA-256 digest"}
    assert queries.validate_runtime_configuration_follow_up(
        conn,
        plan["plan_id"],
        "runtime-configuration-source-import-unknown:checkout-service:config_map:external-config",
        [],
        source_import_truncated=True,
        include_trace_details=1,
    ) == {"error": "include_trace_details must be a boolean"}
    assert queries.validate_runtime_configuration_follow_up(
        conn,
        plan["plan_id"],
        "runtime-configuration-source-import-unknown:checkout-service:config_map:external-config",
        [],
        source_import_truncated=True,
        include_matched_workloads=1,
    ) == {"error": "include_matched_workloads must be a boolean"}
    assert queries.validate_runtime_configuration_follow_up(
        conn,
        plan["plan_id"],
        "runtime-configuration-source-import-unknown:checkout-service:config_map:external-config",
        [{"kind": "Deployment", "name": "other", "container": "api", "extra": object()}],
        source_import_truncated=True,
    ) == {"error": "returned_workloads must contain JSON values"}
    assert queries.validate_runtime_configuration_follow_up(
        conn,
        plan["plan_id"],
        "runtime-configuration-source-import-unknown:checkout-service:config_map:external-config",
        [],
        source_import_truncated=True,
        current_offset=50,
    )["next_query"]["arguments"]["offset"] == 0
    assert queries.validate_runtime_configuration_follow_up(
        conn,
        plan["plan_id"],
        "runtime-configuration-source-import-unknown:checkout-service:config_map:external-config",
        [],
        source_import_truncated=True,
        current_offset=25,
        current_limit=25,
    )["next_query"] == {
        "tool": "describe_runtime_configuration",
        "arguments": {
            "service": "checkout-service", "limit": 25, "offset": 0,
            "workloads": [{"kind": "Deployment", "name": "checkout", "container": "api"}],
        },
    }
    assert queries.validate_runtime_configuration_follow_up(
        conn,
        plan["plan_id"],
        "runtime-configuration-source-import-unknown:checkout-service:config_map:external-config",
        [],
        source_import_truncated=True,
        current_limit=0,
    ) == {"error": "current_limit must be an integer between 1 and 500"}
    assert queries.validate_runtime_configuration_follow_up(
        conn,
        plan["plan_id"],
        "runtime-configuration-source-import-unknown:checkout-service:config_map:external-config",
        [],
        source_import_truncated=True,
        current_offset=30,
        current_limit=25,
    ) == {"error": "current_offset must be a multiple of current_limit"}
    assert queries.validate_runtime_configuration_follow_up(
        conn,
        plan["plan_id"],
        "runtime-configuration-source-import-unknown:checkout-service:config_map:external-config",
        [],
        source_import_truncated=False,
    )["status"] == "incomplete"


def test_truncated_workload_scopes_are_grouped_and_validated():
    assert queries._truncated_workload_scopes({
        "target": {
            "workloads": [
                {"kind": "Deployment", "name": "checkout", "container": "api", "evidence_truncated": True},
                {"kind": "Deployment", "name": "checkout", "container": "api", "evidence_truncated": True},
                {"kind": "Deployment", "name": "checkout", "container": "worker", "evidence_truncated": True},
                {"kind": "Deployment", "name": "checkout", "evidence_truncated": True},
                {"kind": "Deployment", "name": "checkout", "container": "cron", "evidence_truncated": False},
            ],
        },
    }) == [
        {"kind": "Deployment", "name": "checkout", "container": "api"},
        {"kind": "Deployment", "name": "checkout", "container": "worker"},
    ]


def test_truncated_workload_evidence_next_step_uses_count_or_a_safe_legacy_fallback():
    assert queries._truncated_workload_evidence_next_step([{"evidence_total": 5}]) == (
        "inspect_change_unit_evidence"
    )
    assert queries._truncated_workload_evidence_next_step([{"evidence_total": 6}]) == (
        "query_runtime_configuration"
    )
    assert queries._truncated_workload_evidence_next_step([{}]) == "query_runtime_configuration"


def test_evidence_follow_up_adds_pagination_for_a_large_workload_evidence_set():
    assert queries._evidence_follow_up({
        "service": "checkout-service",
        "target": {
            "workloads": [{
                "kind": "Deployment", "name": "checkout", "container": "api",
                "evidence_truncated": True, "evidence_total": 6,
            }],
        },
    }) == {
        "reason": "workload evidence is truncated",
        "recommended_next_step": "query_runtime_configuration",
        "workloads": [{
            "kind": "Deployment", "name": "checkout", "container": "api", "evidence_total": 6,
        }],
        "recommended_query": {
            "tool": "describe_runtime_configuration", "arguments": {"service": "checkout-service"},
        },
        "pagination": {
            "limit": 50, "offset": 0, "next_offset": 50, "continue_when": "source_import_truncated",
            "stop_when": "all_selected_workloads_found",
        },
    }


def test_error_mapping_units_exclude_low_confidence_or_unrelated_error_findings():
    assert derive_error_mapping_review_units([
        {
            "kind": "possible_unmapped_downstream_error",
            "services": ["checkout-service", "payments-service"],
            "confidence": 0.75,
            "detail": {"evidence": [{"file": "client.py", "start_line": 1, "end_line": 1}]},
        },
        {
            "kind": "possible_error_semantics_lost",
            "services": ["payments-service"],
            "confidence": 0.75,
            "detail": {
                "error_type": "PaymentConflict",
                "mapping": {"symbol": "ApiExceptionHandler.handle", "code": "500"},
                "evidence": [{"file": "handler.py", "start_line": 1, "end_line": 1}],
            },
        },
        {
            "kind": "possible_overbroad_exception_handler",
            "services": ["payments-service"],
            "confidence": 0.74,
            "detail": {
                "handler": "ApiExceptionHandler.fallback", "internal_type": "Exception",
                "transport": {"protocol": "http", "code": "500"},
                "evidence": [{"file": "handler.py", "start_line": 4, "end_line": 4}],
            },
        },
        {
            "kind": "possible_timeout_mapped_as_internal_server_error",
            "services": ["payments-service"],
            "confidence": 0.8,
            "detail": {
                "mapping": {"symbol": "ApiExceptionHandler.timeout", "error_type": "TimeoutException", "status": "503"},
                "evidence": [{"file": "handler.py", "start_line": 6, "end_line": 6}],
            },
        },
    ], {"payments-service"}) == []


def test_error_mapping_units_include_a_high_confidence_internal_exposure():
    assert derive_error_mapping_review_units([
        {
            "kind": "possible_internal_error_exposure",
            "services": ["payments-service"],
            "reason": "payments-service exposes a direct internal error detail.",
            "confidence": 0.9,
            "detail": {
                "mapping": {
                    "symbol": "ApiExceptionHandler.handle", "protocol": "http", "code": "500",
                },
                "evidence": [{"file": "handler.py", "start_line": 8, "end_line": 8}],
            },
        },
    ], {"payments-service"}) == [{
        "id": "error-exposure:payments-service:ApiExceptionHandler.handle:http:500",
        "service": "payments-service",
        "target": {
            "role": "error_mapping", "symbol": "ApiExceptionHandler.handle",
            "evidence": [{"file": "handler.py", "start_line": 8, "end_line": 8}],
        },
        "action": "review",
        "reason": "payments-service exposes a direct internal error detail.",
        "preconditions": [],
        "related_contracts": ["HTTP 500"],
        "dependencies": [],
        "validation": [
            "verify the response returns a stable public error code/message and keeps diagnostic detail internal",
        ],
        "confidence": 0.9,
        "evidence": [{"file": "handler.py", "start_line": 8, "end_line": 8}],
    }]


def test_error_mapping_units_include_a_reachable_endpoint_without_a_local_mapping():
    assert derive_error_mapping_review_units([
        {
            "kind": "possible_unhandled_endpoint_error",
            "services": ["payments-service"],
            "reason": "POST /payments can reach PaymentService.authorize without a local mapping.",
            "confidence": 0.75,
            "detail": {
                "entrypoint": {
                    "method": "POST", "path": "/payments", "symbol": "PaymentController.create",
                },
                "origin": {
                    "symbol": "PaymentService.authorize", "error_type": "PaymentConflict", "kind": "conflict",
                },
                "evidence": [
                    {"file": "PaymentController.java", "start_line": 12, "end_line": 12},
                    {"file": "PaymentService.java", "start_line": 28, "end_line": 28},
                ],
            },
        },
    ], {"payments-service"}) == [{
        "id": "error-mapping-gap:payments-service:PaymentController.create:PaymentConflict",
        "service": "payments-service",
        "target": {
            "role": "error_mapping", "symbol": "PaymentController.create",
            "evidence": [
                {"file": "PaymentController.java", "start_line": 12, "end_line": 12},
                {"file": "PaymentService.java", "start_line": 28, "end_line": 28},
            ],
        },
        "action": "review",
        "reason": "POST /payments can reach PaymentService.authorize without a local mapping.",
        "preconditions": [],
        "related_contracts": ["POST /payments", "error:PaymentConflict"],
        "dependencies": [],
        "validation": [
            "verify an indexed or framework-global error mapping returns the documented client response for PaymentConflict at POST /payments",
        ],
        "confidence": 0.75,
        "evidence": [
            {"file": "PaymentController.java", "start_line": 12, "end_line": 12},
            {"file": "PaymentService.java", "start_line": 28, "end_line": 28},
        ],
    }]


def test_error_mapping_units_include_a_proven_downstream_endpoint_contract_gap():
    assert derive_error_mapping_review_units([
        {
            "kind": "possible_unmapped_downstream_error",
            "services": ["checkout-service", "inventory-service"],
            "reason": "checkout-service POST /orders calls inventory-service without a local stock mapping.",
            "confidence": 0.75,
            "detail": {
                "caller": {
                    "service": "checkout-service", "symbol": "CheckoutService.submit",
                    "method": "POST", "path": "/orders",
                },
                "downstream": {
                    "service": "inventory-service", "symbol": "InventoryService.reserve",
                    "error_type": "InsufficientStock", "kind": "conflict",
                    "status": "409", "public_code": "OUT_OF_STOCK",
                },
                "scope": "endpoint_flow",
                "evidence": [
                    {"file": "CheckoutService.java", "start_line": 18, "end_line": 18},
                    {"file": "InventoryService.java", "start_line": 31, "end_line": 31},
                ],
            },
        },
    ], {"checkout-service"}) == [{
        "id": "downstream-error-mapping:checkout-service:CheckoutService.submit:inventory-service:InsufficientStock:409",
        "service": "checkout-service",
        "target": {
            "role": "error_mapping", "symbol": "CheckoutService.submit",
            "evidence": [
                {"file": "CheckoutService.java", "start_line": 18, "end_line": 18},
                {"file": "InventoryService.java", "start_line": 31, "end_line": 31},
            ],
        },
        "action": "review",
        "reason": "checkout-service POST /orders calls inventory-service without a local stock mapping.",
        "preconditions": [],
        "related_contracts": ["POST /orders", "inventory-service HTTP 409 OUT_OF_STOCK", "error:InsufficientStock"],
        "dependencies": ["inventory-service"],
        "validation": [
            "verify CheckoutService.submit or its boundary maps inventory-service HTTP 409 for InsufficientStock to the documented client response",
        ],
        "confidence": 0.75,
        "evidence": [
            {"file": "CheckoutService.java", "start_line": 18, "end_line": 18},
            {"file": "InventoryService.java", "start_line": 31, "end_line": 31},
        ],
    }]


def test_retry_policy_units_include_a_non_retryable_error_in_the_same_symbol():
    assert derive_retry_policy_review_units([
        {
            "kind": "possible_retry_on_non_retryable_error",
            "services": ["orders-service"],
            "reason": "OrderService.create retries a non-retryable validation error.",
            "confidence": 0.8,
            "detail": {
                "error": {
                    "symbol": "OrderService.create", "type": "InvalidOrderException",
                    "kind": "validation", "status": "400",
                },
                "retry_policies": [{"mechanism": "reactor.retry", "value": 3, "unit": "attempts"}],
                "evidence": [
                    {"file": "OrderService.java", "start_line": 17, "end_line": 17},
                    {"file": "OrderService.java", "start_line": 22, "end_line": 22},
                ],
            },
        },
    ], {"orders-service"}) == [{
        "id": "retry-policy:orders-service:OrderService.create:InvalidOrderException",
        "service": "orders-service",
        "target": {
            "role": "application_flow", "symbol": "OrderService.create",
            "evidence": [
                {"file": "OrderService.java", "start_line": 17, "end_line": 17},
                {"file": "OrderService.java", "start_line": 22, "end_line": 22},
            ],
        },
        "action": "review",
        "reason": "OrderService.create retries a non-retryable validation error.",
        "preconditions": [],
        "related_contracts": ["error:InvalidOrderException", "HTTP 400"],
        "dependencies": [],
        "validation": [
            "verify retries in OrderService.create exclude the non-retryable validation error InvalidOrderException",
        ],
        "confidence": 0.8,
        "evidence": [
            {"file": "OrderService.java", "start_line": 17, "end_line": 17},
            {"file": "OrderService.java", "start_line": 22, "end_line": 22},
        ],
    }]


def test_error_mapping_units_include_a_broad_exception_handler_review():
    assert derive_error_mapping_review_units([
        {
            "kind": "possible_overbroad_exception_handler",
            "services": ["orders-service"],
            "reason": "ApiExceptionHandler.fallback maps broad Exception to HTTP 500.",
            "confidence": 0.75,
            "detail": {
                "handler": "ApiExceptionHandler.fallback",
                "internal_type": "Exception",
                "transport": {"protocol": "http", "code": "500"},
                "evidence": [{"file": "ApiExceptionHandler.java", "start_line": 45, "end_line": 49}],
            },
        },
    ], {"orders-service"}) == [{
        "id": "broad-error-handler:orders-service:ApiExceptionHandler.fallback:Exception:http:500",
        "service": "orders-service",
        "target": {
            "role": "error_mapping", "symbol": "ApiExceptionHandler.fallback",
            "evidence": [{"file": "ApiExceptionHandler.java", "start_line": 45, "end_line": 49}],
        },
        "action": "review",
        "reason": "ApiExceptionHandler.fallback maps broad Exception to HTTP 500.",
        "preconditions": [],
        "related_contracts": ["error:Exception", "HTTP 500"],
        "dependencies": [],
        "validation": [
            "verify ApiExceptionHandler.fallback remains a safe fallback and expected client/domain errors have explicit mappings",
        ],
        "confidence": 0.75,
        "evidence": [{"file": "ApiExceptionHandler.java", "start_line": 45, "end_line": 49}],
    }]


def test_error_mapping_units_include_a_timeout_mapped_to_internal_server_error():
    assert derive_error_mapping_review_units([
        {
            "kind": "possible_timeout_mapped_as_internal_server_error",
            "services": ["orders-service"],
            "reason": "ApiExceptionHandler.timeout maps TimeoutException to HTTP 500.",
            "confidence": 0.8,
            "detail": {
                "mapping": {
                    "symbol": "ApiExceptionHandler.timeout", "error_type": "TimeoutException", "status": "500",
                },
                "evidence": [{"file": "ApiExceptionHandler.java", "start_line": 55, "end_line": 58}],
            },
        },
    ], {"orders-service"}) == [{
        "id": "timeout-error-mapping:orders-service:ApiExceptionHandler.timeout:TimeoutException:500",
        "service": "orders-service",
        "target": {
            "role": "error_mapping", "symbol": "ApiExceptionHandler.timeout",
            "evidence": [{"file": "ApiExceptionHandler.java", "start_line": 55, "end_line": 58}],
        },
        "action": "review",
        "reason": "ApiExceptionHandler.timeout maps TimeoutException to HTTP 500.",
        "preconditions": [],
        "related_contracts": ["error:TimeoutException", "HTTP 500"],
        "dependencies": [],
        "validation": [
            "verify TimeoutException uses the documented unavailable or gateway-timeout contract, or document the HTTP 500 translation",
        ],
        "confidence": 0.8,
        "evidence": [{"file": "ApiExceptionHandler.java", "start_line": 55, "end_line": 58}],
    }]


def test_timeout_fallback_units_include_a_successful_endpoint_fallback():
    assert derive_timeout_fallback_review_units([
        {
            "kind": "possible_timeout_fallback_masks_failure",
            "services": ["checkout-service"],
            "reason": "POST /checkout handles TimeoutException from inventory with HTTP 200.",
            "confidence": 0.8,
            "detail": {
                "entrypoint": {
                    "method": "POST", "path": "/checkout", "symbol": "CheckoutController.reserve",
                },
                "target": {"service": "inventory-service", "method": "POST", "path": "/reservations"},
                "fallback": {"error_type": "TimeoutException", "status": "200"},
                "evidence": [
                    {"file": "CheckoutController.java", "start_line": 22, "end_line": 22},
                    {"file": "CheckoutController.java", "start_line": 27, "end_line": 30},
                ],
            },
        },
    ], {"checkout-service"}) == [{
        "id": "timeout-fallback:checkout-service:CheckoutController.reserve:TimeoutException:200",
        "service": "checkout-service",
        "target": {
            "role": "entrypoint", "symbol": "CheckoutController.reserve",
            "evidence": [
                {"file": "CheckoutController.java", "start_line": 22, "end_line": 22},
                {"file": "CheckoutController.java", "start_line": 27, "end_line": 30},
            ],
        },
        "action": "review",
        "reason": "POST /checkout handles TimeoutException from inventory with HTTP 200.",
        "preconditions": [],
        "related_contracts": ["POST /checkout", "HTTP 200", "error:TimeoutException"],
        "dependencies": ["inventory-service"],
        "validation": [
            "verify POST /checkout exposes an explicit degraded-result signal or returns the documented timeout/unavailable contract",
        ],
        "confidence": 0.8,
        "evidence": [
            {"file": "CheckoutController.java", "start_line": 22, "end_line": 22},
            {"file": "CheckoutController.java", "start_line": 27, "end_line": 30},
        ],
    }]


def test_timeout_fallback_units_exclude_non_successful_timeout_translations():
    assert derive_timeout_fallback_review_units([
        {
            "kind": "possible_timeout_fallback_masks_failure",
            "services": ["checkout-service"],
            "confidence": 0.8,
            "detail": {
                "entrypoint": {"method": "POST", "path": "/checkout", "symbol": "CheckoutController.reserve"},
                "target": {"service": "inventory-service"},
                "fallback": {"error_type": "TimeoutException", "status": "503"},
                "evidence": [{"file": "CheckoutController.java", "start_line": 27, "end_line": 30}],
            },
        },
    ], {"checkout-service"}) == []


def test_read_entrypoint_side_effect_units_include_a_static_get_write():
    assert derive_read_entrypoint_side_effect_review_units([
        {
            "kind": "possible_read_entrypoint_side_effect",
            "services": ["catalog-service"],
            "reason": "GET /catalog/refresh writes state.",
            "confidence": 0.8,
            "detail": {
                "entrypoint": {
                    "kind": "http", "method": "GET", "name": "/catalog/refresh", "symbol": "Catalog.refresh",
                },
                "operations": [{"kind": "writes", "target": "repository.save"}],
                "evidence": [{"file": "CatalogController.java", "start_line": 14, "end_line": 14}],
            },
        },
    ], {"catalog-service"}) == [{
        "id": "read-entrypoint-side-effect:catalog-service:http:GET:Catalog.refresh",
        "service": "catalog-service",
        "target": {
            "role": "entrypoint", "symbol": "Catalog.refresh",
            "evidence": [{"file": "CatalogController.java", "start_line": 14, "end_line": 14}],
        },
        "action": "review",
        "reason": "GET /catalog/refresh writes state.",
        "preconditions": [],
        "related_contracts": ["GET /catalog/refresh"],
        "dependencies": [],
        "validation": [
            "verify GET /catalog/refresh has no externally observable side effect, or document its cache, metric, or legacy exception",
        ],
        "confidence": 0.8,
        "evidence": [{"file": "CatalogController.java", "start_line": 14, "end_line": 14}],
    }]


def test_read_entrypoint_side_effect_units_label_graphql_queries():
    units = derive_read_entrypoint_side_effect_review_units([
        {
            "kind": "possible_read_entrypoint_side_effect",
            "services": ["catalog-service"],
            "confidence": 0.8,
            "detail": {
                "entrypoint": {"kind": "graphql", "method": "QUERY", "name": "catalog", "symbol": "Query.catalog"},
                "operations": [{"kind": "publishes", "target": "events.publish"}],
                "evidence": [{"file": "resolvers.ts", "start_line": 27, "end_line": 27}],
            },
        },
    ], {"catalog-service"})

    assert units[0]["id"] == "read-entrypoint-side-effect:catalog-service:graphql:QUERY:Query.catalog"
    assert units[0]["related_contracts"] == ["GRAPHQL QUERY catalog"]
    assert units[0]["validation"] == [
        "verify GRAPHQL QUERY catalog has no externally observable side effect, or document its cache, metric, or legacy exception",
    ]


def test_public_object_storage_units_include_a_literal_public_iac_setting():
    assert derive_public_object_storage_review_units([
        {
            "kind": "possible_public_object_storage",
            "services": ["media-service"],
            "reason": "media-service storage 'public-assets' declares acl='public-read'.",
            "confidence": 0.85,
            "detail": {
                "bucket": "public-assets", "attribute": "acl", "value": "public-read",
                "evidence": [{"file": "infra/storage.tf", "start_line": 8, "end_line": 14}],
            },
        },
    ], {"media-service"}) == [{
        "id": "public-object-storage:media-service:public-assets",
        "service": "media-service",
        "target": {
            "role": "deployment", "symbol": "object-storage:public-assets",
            "evidence": [{"file": "infra/storage.tf", "start_line": 8, "end_line": 14}],
        },
        "action": "review",
        "reason": "media-service storage 'public-assets' declares acl='public-read'.",
        "preconditions": [],
        "related_contracts": ["cloud:object_storage:public-assets"],
        "dependencies": [],
        "validation": [
            "verify public access for object storage public-assets is intentional, or use a private ACL with explicit access policy",
        ],
        "confidence": 0.85,
        "evidence": [{"file": "infra/storage.tf", "start_line": 8, "end_line": 14}],
    }]


def test_public_object_storage_units_exclude_low_confidence_findings():
    assert derive_public_object_storage_review_units([
        {
            "kind": "possible_public_object_storage",
            "services": ["media-service"],
            "confidence": 0.84,
            "detail": {
                "bucket": "public-assets", "attribute": "acl", "value": "public-read",
                "evidence": [{"file": "infra/storage.tf", "start_line": 8, "end_line": 14}],
            },
        },
    ], {"media-service"}) == []


def test_retry_delivery_units_include_persistent_message_consumers():
    assert derive_retry_delivery_review_units([
        {
            "kind": "possible_retry_write_publish_reaches_persistent_consumer",
            "services": ["orders-service", "billing-service"],
            "reason": "OrderService.create retries publication to a state-writing billing consumer.",
            "confidence": 0.8,
            "detail": {
                "flow": {"symbol": "OrderService.create"},
                "channel": "order.created",
                "retry_policies": [{"mechanism": "reactor.retry", "value": 3, "unit": "attempts"}],
                "writes": [{"target": "orders"}], "write_count": 1,
                "consumers": [{
                    "service": "billing-service", "symbol": "BillingConsumer.onOrderCreated",
                    "writes": [{"target": "invoices"}], "write_count": 1,
                }],
                "consumer_count": 1,
                "evidence": [
                    {"file": "OrderService.java", "start_line": 18, "end_line": 18},
                    {"file": "BillingConsumer.java", "start_line": 12, "end_line": 12},
                ],
            },
        },
    ], {"orders-service"}) == [{
        "id": "retry-delivery:orders-service:OrderService.create:order.created",
        "service": "orders-service",
        "target": {
            "role": "application_flow", "symbol": "OrderService.create",
            "evidence": [
                {"file": "OrderService.java", "start_line": 18, "end_line": 18},
                {"file": "BillingConsumer.java", "start_line": 12, "end_line": 12},
            ],
        },
        "action": "review",
        "reason": "OrderService.create retries publication to a state-writing billing consumer.",
        "preconditions": [],
        "related_contracts": ["message:order.created"],
        "dependencies": ["billing-service"],
        "validation": [
            "verify OrderService.create uses an outbox or idempotency strategy before retrying order.created",
            "verify billing-service de-duplicates persistent effects for order.created",
        ],
        "confidence": 0.8,
        "evidence": [
            {"file": "OrderService.java", "start_line": 18, "end_line": 18},
            {"file": "BillingConsumer.java", "start_line": 12, "end_line": 12},
        ],
    }]


def test_retry_delivery_units_exclude_low_confidence_findings():
    assert derive_retry_delivery_review_units([
        {
            "kind": "possible_retry_write_publish_reaches_persistent_consumer",
            "services": ["orders-service", "billing-service"],
            "confidence": 0.79,
            "detail": {},
        },
    ], {"orders-service"}) == []


def test_retry_downstream_error_units_include_a_resolved_endpoint_contract():
    assert derive_retry_downstream_error_review_units([
        {
            "kind": "possible_retry_on_downstream_client_error",
            "services": ["checkout-service", "inventory-service"],
            "reason": "CheckoutService.submit retries inventory-service HTTP 409.",
            "confidence": 0.8,
            "detail": {
                "caller": {
                    "service": "checkout-service", "symbol": "CheckoutService.submit",
                    "method": "POST", "path": "/orders",
                },
                "downstream": {
                    "service": "inventory-service", "symbol": "InventoryService.reserve",
                    "error_type": "InsufficientStock", "kind": "conflict", "status": "409",
                },
                "retry_policies": [{"mechanism": "reactor.retry", "value": 3, "unit": "attempts"}],
                "scope": "endpoint_flow",
                "evidence": [
                    {"file": "CheckoutService.java", "start_line": 18, "end_line": 18},
                    {"file": "InventoryService.java", "start_line": 31, "end_line": 31},
                ],
            },
        },
    ], {"checkout-service"}) == [{
        "id": "retry-downstream-error:checkout-service:CheckoutService.submit:inventory-service:InsufficientStock:409",
        "service": "checkout-service",
        "target": {
            "role": "application_flow", "symbol": "CheckoutService.submit",
            "evidence": [
                {"file": "CheckoutService.java", "start_line": 18, "end_line": 18},
                {"file": "InventoryService.java", "start_line": 31, "end_line": 31},
            ],
        },
        "action": "review",
        "reason": "CheckoutService.submit retries inventory-service HTTP 409.",
        "preconditions": [],
        "related_contracts": ["POST /orders", "inventory-service HTTP 409", "error:InsufficientStock"],
        "dependencies": ["inventory-service"],
        "validation": [
            "verify retries in CheckoutService.submit exclude inventory-service HTTP 409 for InsufficientStock unless its contract explicitly marks it transient",
        ],
        "confidence": 0.8,
        "evidence": [
            {"file": "CheckoutService.java", "start_line": 18, "end_line": 18},
            {"file": "InventoryService.java", "start_line": 31, "end_line": 31},
        ],
    }]


def test_retry_downstream_error_units_exclude_service_wide_contracts():
    assert derive_retry_downstream_error_review_units([
        {
            "kind": "possible_retry_on_downstream_client_error",
            "services": ["checkout-service", "inventory-service"],
            "confidence": 0.8,
            "detail": {"scope": "service_contracts"},
        },
    ], {"checkout-service"}) == []


def test_partial_write_resilience_units_include_local_writes_and_remote_call():
    assert derive_partial_write_resilience_review_units([
        {
            "kind": "possible_resilience_policy_on_partial_write_flow",
            "services": ["orders-service"],
            "reason": "OrderService.create writes locally and calls inventory under retry.",
            "confidence": 0.7,
            "detail": {
                "flow": {"symbol": "OrderService.create"},
                "target": {"service": "inventory-service", "method": "POST", "path": "/reservations"},
                "resilience_policies": [{"kind": "retry", "mechanism": "reactor.retry", "value": 3, "unit": "attempts"}],
                "writes": [{"target": "orders"}], "write_count": 1,
                "evidence": [
                    {"file": "OrderService.java", "start_line": 18, "end_line": 18},
                    {"file": "OrderService.java", "start_line": 22, "end_line": 22},
                ],
            },
        },
    ], {"orders-service"}) == [{
        "id": "partial-write-resilience:orders-service:OrderService.create:inventory-service",
        "service": "orders-service",
        "target": {
            "role": "application_flow", "symbol": "OrderService.create",
            "evidence": [
                {"file": "OrderService.java", "start_line": 18, "end_line": 18},
                {"file": "OrderService.java", "start_line": 22, "end_line": 22},
            ],
        },
        "action": "review",
        "reason": "OrderService.create writes locally and calls inventory under retry.",
        "preconditions": [],
        "related_contracts": ["POST /reservations"],
        "dependencies": ["inventory-service"],
        "validation": [
            "verify ordering, idempotency, and recovery for writes in OrderService.create and its inventory-service call",
            "verify a transaction, outbox, compensation, or retry-safe contract protects partial effects",
        ],
        "confidence": 0.7,
        "evidence": [
            {"file": "OrderService.java", "start_line": 18, "end_line": 18},
            {"file": "OrderService.java", "start_line": 22, "end_line": 22},
        ],
    }]


def test_partial_write_resilience_units_exclude_low_confidence_findings():
    assert derive_partial_write_resilience_review_units([
        {
            "kind": "possible_resilience_policy_on_partial_write_flow",
            "services": ["orders-service"],
            "confidence": 0.69,
            "detail": {},
        },
    ], {"orders-service"}) == []


def test_retry_write_publish_units_include_uncovered_literal_channels():
    assert derive_retry_write_publish_review_units([
        {
            "kind": "possible_retry_on_write_publish_flow",
            "services": ["orders-service"],
            "reason": "OrderService.create retries publication after a local write.",
            "confidence": 0.7,
            "detail": {
                "flow": {"symbol": "OrderService.create"},
                "retry_policies": [{"mechanism": "reactor.retry", "value": 3, "unit": "attempts"}],
                "writes": [{"target": "orders"}], "write_count": 1,
                "publishes": [{"target": "order.created"}], "publish_count": 1,
                "evidence": [{"file": "OrderService.java", "start_line": 18, "end_line": 22}],
            },
        },
    ], {"orders-service"}) == [{
        "id": "retry-write-publish:orders-service:OrderService.create:order.created",
        "service": "orders-service",
        "target": {
            "role": "application_flow", "symbol": "OrderService.create",
            "evidence": [{"file": "OrderService.java", "start_line": 18, "end_line": 22}],
        },
        "action": "review",
        "reason": "OrderService.create retries publication after a local write.",
        "preconditions": [],
        "related_contracts": ["message:order.created"],
        "dependencies": [],
        "validation": [
            "verify OrderService.create uses an outbox or idempotency strategy before retrying publication to order.created",
            "verify duplicate-event and partial-effect handling for order.created",
        ],
        "confidence": 0.7,
        "evidence": [{"file": "OrderService.java", "start_line": 18, "end_line": 22}],
    }]


def test_retry_write_publish_units_yield_to_a_consumer_aware_finding_for_the_same_channel():
    generic = {
        "kind": "possible_retry_on_write_publish_flow",
        "services": ["orders-service"],
        "confidence": 0.7,
        "detail": {
            "flow": {"symbol": "OrderService.create"},
            "retry_policies": [{"mechanism": "reactor.retry"}],
            "writes": [{"target": "orders"}], "publishes": [{"target": "order.created"}],
            "evidence": [{"file": "OrderService.java", "start_line": 18, "end_line": 22}],
        },
    }
    consumer_aware = {
        "kind": "possible_retry_write_publish_reaches_consumer",
        "services": ["orders-service", "billing-service"],
        "detail": {"flow": {"symbol": "OrderService.create"}, "channel": "order.created"},
    }

    assert derive_retry_write_publish_review_units([generic, consumer_aware], {"orders-service"}) == []


def test_runtime_configuration_units_require_an_exact_environment_key_match():
    assert derive_runtime_configuration_review_units(
        {"checkout-service": [{
            "key": "ORDERS_TOPIC", "kind": "environment", "file_path": "checkout.py",
            "start_line": 3, "end_line": 3,
        }, {
            "key": "orders.topic", "kind": "property", "file_path": "checkout.py",
            "start_line": 4, "end_line": 4,
        }]},
        {"checkout-service": [{
            "environment_key": "PAYMENTS_TOPIC", "source_kind": "config_map", "source_name": "checkout-config",
            "source_key": "payments-topic", "file_path": "deploy/checkout.yaml", "start_line": 8, "end_line": 12,
        }]},
        {"checkout-service"},
    ) == []


def test_migration_units_require_an_exact_affected_table_match_with_source_evidence():
    assert derive_persistence_migration_review_units(
        [{
            "service": "payments-service", "entity": "payment_method", "kind": "sql_table",
            "evidence": [{"file": "models.py", "start_line": 2, "end_line": 2}],
        }],
        {"payments-service": [{
            "operation": "drop_column", "table_name": "invoices", "column_name": "legacy_id",
            "destructive": 1, "file_path": "db/migration/V2.sql", "start_line": 1, "end_line": 1,
        }]},
        {"payments-service"},
    ) == []
    assert derive_persistence_migration_review_units(
        [{"service": "payments-service", "entity": "payment_method", "kind": "sql_table", "evidence": []}],
        {"payments-service": [{
            "operation": "drop_column", "table_name": "payment_method", "column_name": "legacy_id",
            "destructive": 1, "file_path": "db/migration/V2.sql", "start_line": 1, "end_line": 1,
        }]},
        {"payments-service"},
    ) == []


def test_describe_change_unit_uses_http_specific_minimal_queries(tmp_path):
    conn = _build_pix_fixture(tmp_path / "http-unit-detail.db")
    checkout = services_repo.get_service_by_name(conn, "checkout-service")
    payments = services_repo.get_service_by_name(conn, "payments-service")
    evidence = Evidence("CheckoutService.java", 18, 18)
    flows_repo.replace_analysis(conn, checkout["id"], AnalysisResult(static_service_calls=[
        StaticServiceCall(
            source="CheckoutService.submit", target_service="payments-service", protocol="http",
            target_method="POST", target_path="/authorizations", evidence=evidence,
        ),
    ]))
    flows_repo.replace_analysis(conn, payments["id"], AnalysisResult(entrypoints=[
        EntryPoint("http", "POST", "/authorizations", "PaymentsController.authorize", evidence),
    ]))
    plan = queries.plan_change(conn, FakeBackend({
        "primary": [{"service": "checkout-service", "reason": "owns checkout", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    }), "Add a payment method")

    result = queries.describe_change_unit(
        conn, plan["plan_id"], "http-contract:checkout-service:payments-service:POST:/authorizations",
    )

    assert result["minimal_reading"] == [
        {
            "service": "checkout-service",
            "purpose": "confirm the literal outbound HTTP client",
            "recommended_query": {"tool": "describe_service", "arguments": {"service": "checkout-service"}},
        },
        {
            "service": "payments-service",
            "purpose": "confirm the resolved target endpoint contract",
            "recommended_query": {"tool": "list_entrypoints", "arguments": {"service": "payments-service"}},
        },
    ]
    validate(result, load_schema("describe_change_unit"))


def test_plan_change_requires_a_contract_compatibility_decision_for_affected_event_consumers(tmp_path):
    conn = _build_pix_fixture(tmp_path / "event-decision.db")
    backend = FakeBackend({
        "primary": [{"service": "payments-service", "reason": "owns payment authorization", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    })

    result = queries.plan_change(conn, backend, "Add a payment method")

    assert result["status"] == "needs_decision"
    assert result["decision_points"] == [{
        "id": "event-compatibility:payments-service:payment_authorized",
        "question": "Will the payment_authorized event payload or compatibility change?",
        "why_blocking": "notification-service consumes this event; compatibility determines whether it must change.",
        "options": [
            "preserve backward compatibility",
            "version the event contract and update consumers",
        ],
        "recommended_default": "preserve backward compatibility unless a versioned rollout is approved",
        "owner": "payments-service",
        "contract": "payment_authorized",
        "consumers": ["notification-service"],
        "evidence": [],
    }]
    validate(result, load_schema("plan_change"))

    plan = change_plans.get_plan(conn, int(result["plan_id"].removeprefix("cp_")))
    assert json.loads(plan["decision_points_json"]) == result["decision_points"]


def test_refine_change_plan_persists_an_explicit_decision_without_retrieval(tmp_path):
    conn = _build_pix_fixture(tmp_path / "refine.db")
    backend = FakeBackend({
        "primary": [{"service": "payments-service", "reason": "owns payment authorization", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    })
    plan = queries.plan_change(conn, backend, "Add a payment method")

    result = queries.refine_change_plan(conn, plan["plan_id"], [{
        "id": "event-compatibility:payments-service:payment_authorized",
        "option": "preserve backward compatibility",
    }])

    assert backend.calls == 1
    assert result == {
        "plan_id": plan["plan_id"],
        "status": "ready",
        "selected_decisions": [{
            "id": "event-compatibility:payments-service:payment_authorized",
            "option": "preserve backward compatibility",
        }],
        "remaining_decision_points": [],
        "change_units": [{
            "id": "event-contract:payments-service:payment_authorized",
            "service": "payments-service",
            "target": {
                "role": "contract",
                "symbol": "message.publish:payment_authorized",
                "evidence": [],
            },
            "action": "validate",
            "reason": "preserve backward compatibility for payment_authorized before changing its producer.",
            "preconditions": [
                "event-compatibility:payments-service:payment_authorized=preserve backward compatibility",
            ],
            "related_contracts": ["payment_authorized"],
            "dependencies": ["notification-service"],
            "validation": [
                "verify payment_authorized remains compatible with notification-service",
            ],
            "confidence": 1.0,
            "evidence": [],
        }],
    }
    validate(result, load_schema("refine_change_plan"))
    persisted = change_plans.get_plan(conn, int(plan["plan_id"].removeprefix("cp_")))
    assert persisted["status"] == "ready"
    assert json.loads(persisted["selected_decisions_json"]) == result["selected_decisions"]
    assert json.loads(persisted["change_units_json"]) == result["change_units"]


def test_refine_change_plan_rejects_an_undeclared_option_without_mutating_the_plan(tmp_path):
    conn = _build_pix_fixture(tmp_path / "invalid-refine.db")
    plan = queries.plan_change(conn, FakeBackend({
        "primary": [{"service": "payments-service", "reason": "owns payment authorization", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    }), "Add a payment method")

    result = queries.refine_change_plan(conn, plan["plan_id"], [{
        "id": "event-compatibility:payments-service:payment_authorized",
        "option": "break all consumers",
    }])

    assert result == {"error": "unsupported option for decision: event-compatibility:payments-service:payment_authorized"}
    persisted = change_plans.get_plan(conn, int(plan["plan_id"].removeprefix("cp_")))
    assert persisted["status"] == "needs_decision"
    assert json.loads(persisted["selected_decisions_json"]) == []


def test_refine_change_plan_marks_the_contract_unit_for_modification_when_versioning_is_selected(tmp_path):
    conn = _build_pix_fixture(tmp_path / "versioned-event.db")
    plan = queries.plan_change(conn, FakeBackend({
        "primary": [{"service": "payments-service", "reason": "owns payment authorization", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    }), "Add a payment method")

    result = queries.refine_change_plan(conn, plan["plan_id"], [{
        "id": "event-compatibility:payments-service:payment_authorized",
        "option": "version the event contract and update consumers",
    }])

    unit = result["change_units"][0]
    assert unit["action"] == "modify"
    assert unit["reason"] == "version the event contract for payment_authorized before changing its producer."
    assert unit["dependencies"] == ["notification-service"]


def test_describe_change_unit_returns_only_the_persisted_unit_and_minimal_queries(tmp_path):
    conn = _build_pix_fixture(tmp_path / "unit-detail.db")
    plan = queries.plan_change(conn, FakeBackend({
        "primary": [{"service": "payments-service", "reason": "owns payment authorization", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    }), "Add a payment method")
    refined = queries.refine_change_plan(conn, plan["plan_id"], [{
        "id": "event-compatibility:payments-service:payment_authorized",
        "option": "preserve backward compatibility",
    }])

    result = queries.describe_change_unit(
        conn, plan["plan_id"], "event-contract:payments-service:payment_authorized",
    )

    assert result == {
        "plan_id": plan["plan_id"],
        "change_unit": refined["change_units"][0],
        "minimal_reading": [
            {
                "service": "payments-service",
                "purpose": "confirm the producer contract",
                "recommended_query": {"tool": "describe_messages", "arguments": {"service": "payments-service"}},
            },
            {
                "service": "notification-service",
                "purpose": "confirm consumer compatibility",
                "recommended_query": {"tool": "describe_messages", "arguments": {"service": "notification-service"}},
            },
        ],
        "validation": ["verify payment_authorized remains compatible with notification-service"],
    }
    validate(result, load_schema("describe_change_unit"))


def test_plan_change_persists_an_insufficient_evidence_plan_without_a_surface_run(tmp_path):
    conn = open_db(tmp_path / "empty.db")

    result = queries.plan_change(conn, FakeBackend({}), "unrelated xyz")

    plan = change_plans.get_plan(conn, int(result["plan_id"].removeprefix("cp_")))
    assert result["status"] == "insufficient_evidence"
    assert plan["change_surface_run_id"] is None
    assert plan["status"] == "insufficient_evidence"
