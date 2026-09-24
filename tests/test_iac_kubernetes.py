from pathlib import Path

from orbitkb.iac.kubernetes import (
    is_helm_template,
    parse_kubernetes_configuration_bindings_file,
)
from orbitkb.iac.models import KubernetesConfigurationBinding


def test_plain_manifest_is_not_a_helm_template(tmp_path: Path):
    manifest = tmp_path / "deployment.yaml"
    text = "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: orders\n"
    manifest.write_text(text)

    assert is_helm_template(manifest, text) is False


def test_go_template_syntax_is_detected_regardless_of_location(tmp_path: Path):
    manifest = tmp_path / "deployment.yaml"
    text = "metadata:\n  name: {{ .Values.name }}\n"
    manifest.write_text(text)

    assert is_helm_template(manifest, text) is True


def test_file_under_templates_dir_with_sibling_chart_yaml_is_a_helm_template(tmp_path: Path):
    chart_root = tmp_path / "my-chart"
    templates_dir = chart_root / "templates"
    templates_dir.mkdir(parents=True)
    (chart_root / "Chart.yaml").write_text("apiVersion: v2\nname: my-chart\n")
    manifest = templates_dir / "configmap.yaml"
    text = "data:\n  key: static-value\n"  # no {{ }} in this one file
    manifest.write_text(text)

    assert is_helm_template(manifest, text) is True


def test_file_under_templates_dir_without_chart_yaml_is_not_flagged(tmp_path: Path):
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    manifest = templates_dir / "configmap.yaml"
    text = "data:\n  key: static-value\n"
    manifest.write_text(text)

    assert is_helm_template(manifest, text) is False


def test_kubernetes_parser_extracts_literal_deployment_environment_references(tmp_path: Path):
    manifest = tmp_path / "deployment.yaml"
    manifest.write_text(
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n"
        "  name: orders\n"
        "spec:\n"
        "  template:\n"
        "    spec:\n"
        "      containers:\n"
        "        - name: api\n"
        "          image: example/orders\n"
        "          env:\n"
        "            - name: STRIPE_SECRET_KEY\n"
        "              valueFrom:\n"
        "                secretKeyRef:\n"
        "                  name: payments-secrets\n"
        "                  key: stripe-key\n"
        "            - name: ORDERS_TOPIC\n"
        "              valueFrom:\n"
        "                configMapKeyRef:\n"
        "                  name: orders-config\n"
        "                  key: orders-topic\n"
    )

    assert parse_kubernetes_configuration_bindings_file(manifest) == [
        KubernetesConfigurationBinding(
            environment_key="STRIPE_SECRET_KEY", source_kind="secret", source_name="payments-secrets",
            source_key="stripe-key", workload_kind="Deployment", workload_name="orders", container_name="api",
            file_path=str(manifest), start_line=12, end_line=17,
        ),
        KubernetesConfigurationBinding(
            environment_key="ORDERS_TOPIC", source_kind="config_map", source_name="orders-config",
            source_key="orders-topic", workload_kind="Deployment", workload_name="orders", container_name="api",
            file_path=str(manifest), start_line=17, end_line=22,
        ),
    ]
