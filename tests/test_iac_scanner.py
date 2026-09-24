from pathlib import Path

from orbitkb.discovery.node_ts import NodeTsDetector
from orbitkb.discovery.walker import ServiceCandidate
from orbitkb.iac.kubernetes import parse_kubernetes_configuration_sources_file
from orbitkb.iac.scanner import scan_repository, scan_repository_facts


def _candidate(name: str, path: Path) -> ServiceCandidate:
    return ServiceCandidate(name=name, path=path, detector=NodeTsDetector())


def test_terraform_outside_any_service_root_is_repository_scoped(tmp_path: Path):
    infra = tmp_path / "infra"
    infra.mkdir()
    (infra / "main.tf").write_text(
        'resource "aws_sqs_queue" "orders" {\n  name = "orders-queue"\n}\n'
    )
    orders_service = tmp_path / "orders-service"
    orders_service.mkdir()

    resources = scan_repository(tmp_path, [_candidate("orders-service", orders_service)])

    assert len(resources) == 1
    assert resources[0].matched_service_name is None


def test_terraform_inside_exactly_one_service_root_is_attributed(tmp_path: Path):
    orders_service = tmp_path / "orders-service"
    (orders_service / "infra").mkdir(parents=True)
    (orders_service / "infra" / "main.tf").write_text(
        'resource "aws_sqs_queue" "orders" {\n  name = "orders-queue"\n}\n'
    )
    other_service = tmp_path / "payments-service"
    other_service.mkdir()

    resources = scan_repository(
        tmp_path, [_candidate("orders-service", orders_service), _candidate("payments-service", other_service)],
    )

    assert len(resources) == 1
    assert resources[0].matched_service_name == "orders-service"


def test_cloudformation_and_terraform_are_both_scanned(tmp_path: Path):
    (tmp_path / "main.tf").write_text(
        'resource "aws_sqs_queue" "orders" {\n  name = "orders-queue"\n}\n'
    )
    (tmp_path / "template.yaml").write_text(
        "Resources:\n  AssetsBucket:\n    Type: AWS::S3::Bucket\n"
        "    Properties:\n      BucketName: assets\n"
    )

    resources = scan_repository(tmp_path, [])

    assert {r.iac_resource_type for r in resources} == {"aws_sqs_queue", "AWS::S3::Bucket"}


def test_plain_kubernetes_manifest_yields_no_resources_and_is_not_mis_parsed(tmp_path: Path):
    (tmp_path / "deployment.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: orders\n"
    )

    assert scan_repository(tmp_path, []) == []


def test_kubernetes_environment_references_are_attributed_to_the_containing_service(tmp_path: Path):
    orders_service = tmp_path / "orders-service"
    orders_service.mkdir()
    (orders_service / "deployment.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: orders\nspec:\n"
        "  template:\n    spec:\n      containers:\n        - name: api\n          env:\n"
        "            - name: ORDERS_TOPIC\n              valueFrom:\n                configMapKeyRef:\n"
        "                  name: orders-config\n                  key: orders-topic\n"
    )

    facts = scan_repository_facts(tmp_path, [_candidate("orders-service", orders_service)])

    assert facts.resources == []
    assert [(binding.environment_key, binding.source_kind, binding.source_name, binding.source_key,
             binding.matched_service_name) for binding in facts.configuration_bindings] == [
        ("ORDERS_TOPIC", "config_map", "orders-config", "orders-topic", "orders-service"),
    ]


def test_kubernetes_scanner_reports_a_missing_key_only_for_one_local_source_declaration(tmp_path: Path):
    service = tmp_path / "orders-service"
    service.mkdir()
    (service / "deployment.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: orders\nspec:\n"
        "  template:\n    spec:\n      containers:\n        - name: api\n          env:\n"
        "            - name: ORDERS_TOPIC\n              valueFrom:\n                configMapKeyRef:\n"
        "                  name: orders-config\n                  key: orders-topic\n"
    )
    (service / "config.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: orders-config\ndata:\n  other-key: value\n"
    )

    facts = scan_repository_facts(tmp_path, [_candidate("orders-service", service)])

    assert [(issue.environment_key, issue.source_kind, issue.source_name, issue.source_key,
             issue.matched_service_name) for issue in facts.configuration_key_mismatches] == [
        ("ORDERS_TOPIC", "config_map", "orders-config", "orders-topic", "orders-service"),
    ]


def test_kubernetes_scanner_marks_a_source_absent_from_local_yaml_as_unknown(tmp_path: Path):
    service = tmp_path / "orders-service"
    service.mkdir()
    (service / "deployment.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: orders\nspec:\n"
        "  template:\n    spec:\n      containers:\n        - name: api\n          env:\n"
        "            - name: ORDERS_TOPIC\n              valueFrom:\n                configMapKeyRef:\n"
        "                  name: externally-managed-config\n                  key: orders-topic\n"
    )

    facts = scan_repository_facts(tmp_path, [_candidate("orders-service", service)])

    assert [(issue.environment_key, issue.source_kind, issue.source_name, issue.source_key,
             issue.matched_service_name) for issue in facts.configuration_source_unknowns] == [
        ("ORDERS_TOPIC", "config_map", "externally-managed-config", "orders-topic", "orders-service"),
    ]


def test_kubernetes_scanner_marks_an_env_from_source_absent_from_local_yaml_as_unknown(tmp_path: Path):
    service = tmp_path / "orders-service"
    service.mkdir()
    (service / "deployment.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: orders\nspec:\n"
        "  template:\n    spec:\n      containers:\n        - name: api\n          envFrom:\n"
        "            - configMapRef:\n                name: externally-managed-config\n"
    )

    facts = scan_repository_facts(tmp_path, [_candidate("orders-service", service)])

    assert [(issue.source_kind, issue.source_name, issue.prefix, issue.matched_service_name)
            for issue in facts.configuration_source_import_unknowns] == [
        ("config_map", "externally-managed-config", None, "orders-service"),
    ]


def test_kubernetes_source_file_parses_a_secret_declaration_as_secret_kind(tmp_path: Path):
    secret_file = tmp_path / "secret.yaml"
    secret_file.write_text(
        "apiVersion: v1\nkind: Secret\nmetadata:\n  name: orders-credentials\n"
        "stringData:\n  db-password: unused-in-source\n"
    )

    sources = parse_kubernetes_configuration_sources_file(secret_file)

    assert [(source.source_kind, source.source_name, source.keys) for source in sources] == [
        ("secret", "orders-credentials", ("db-password",)),
    ]


def test_helm_template_is_skipped_not_mis_parsed(tmp_path: Path):
    chart_root = tmp_path / "chart"
    templates_dir = chart_root / "templates"
    templates_dir.mkdir(parents=True)
    (chart_root / "Chart.yaml").write_text("apiVersion: v2\nname: chart\n")
    (templates_dir / "deployment.yaml").write_text("metadata:\n  name: {{ .Values.name }}\n")

    assert scan_repository(tmp_path, []) == []


def test_compose_localstack_hint_is_included(tmp_path: Path):
    (tmp_path / "docker-compose.yaml").write_text(
        "services:\n  localstack:\n    image: localstack/localstack\n"
        "    environment:\n      - SERVICES=sqs\n"
    )

    resources = scan_repository(tmp_path, [])

    assert len(resources) == 1
    assert resources[0].source_format == "compose_hint"


def test_skip_dirs_are_not_walked(tmp_path: Path):
    node_modules = tmp_path / "node_modules" / "some-pkg"
    node_modules.mkdir(parents=True)
    (node_modules / "main.tf").write_text(
        'resource "aws_sqs_queue" "orders" {\n  name = "orders-queue"\n}\n'
    )

    assert scan_repository(tmp_path, []) == []
