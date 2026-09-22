from pathlib import Path

from orbitkb.iac.kubernetes import is_helm_template


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
