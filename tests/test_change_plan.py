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
    derive_persistence_migration_review_units,
    derive_runtime_configuration_review_units,
)
from orbitkb.generation.llm_harness import load_schema
from orbitkb.iac.models import (
    KubernetesConfigurationBinding,
    KubernetesConfigurationKeyMismatch,
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
    assert result["budget"]["truncated"] is False
    validate(result, load_schema("plan_change"))

    plan = change_plans.get_plan(conn, int(result["plan_id"].removeprefix("cp_")))
    assert plan["change_surface_run_id"] is not None
    assert plan["status"] == "ready"
    assert plan["requested_tokens"] == 2200
    assert plan["estimated_tokens"] == result["budget"]["estimated_tokens"]
    assert plan["truncated"] == 0


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
    ], {"payments-service"}) == []


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
