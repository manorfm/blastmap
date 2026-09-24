"""Plain Kubernetes manifest handling.

A manifest describes how to run software, not which cloud resources exist —
unlike Terraform/CloudFormation, it has no field that declares "this creates
an SQS queue" — so it contributes no `IacResource` on its own in v1. Its real
job here is telling a genuine manifest apart from an unrendered Helm chart
template, which is Go template syntax and not valid YAML on its own: silently
parsing `{{ .Values.x }}` as if it were plain YAML would misread the file
rather than skip it.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from orbitkb.iac.models import (
    KubernetesConfigurationBinding,
    KubernetesConfigurationSource,
)

_TEMPLATE_MARKER = "{{"
_POD_TEMPLATE_PATHS = {
    "CronJob": ("spec", "jobTemplate", "spec", "template", "spec"),
    "DaemonSet": ("spec", "template", "spec"),
    "Deployment": ("spec", "template", "spec"),
    "Job": ("spec", "template", "spec"),
    "StatefulSet": ("spec", "template", "spec"),
}


def is_helm_template(path: Path, text: str) -> bool:
    """True when `path` is an unrendered Helm chart template: either its
    content contains Go template syntax, or it sits under a `templates/`
    directory that has a sibling `Chart.yaml` at the chart root — a
    structural marker, not a name guess, since a static file can live in a
    chart's `templates/` folder without itself using `{{ }}`."""
    if _TEMPLATE_MARKER in text:
        return True
    for parent in path.parents:
        if parent.name == "templates" and (parent.parent / "Chart.yaml").is_file():
            return True
    return False


def parse_kubernetes_configuration_bindings_file(path: Path) -> list[KubernetesConfigurationBinding]:
    """Read literal ``env`` references from supported Kubernetes Pod templates.

    Parsing YAML nodes retains evidence locations without relying on text matching.
    Only a fully literal variable name, resource name and key are retained.
    """
    text = path.read_text()
    try:
        documents = list(yaml.compose_all(text))
    except yaml.YAMLError:
        return []
    bindings: list[KubernetesConfigurationBinding] = []
    for document in documents:
        if not isinstance(document, MappingNode):
            continue
        workload_kind = _scalar(_mapping_value(document, "kind"))
        template_path = _POD_TEMPLATE_PATHS.get(workload_kind)
        metadata = _mapping_value(document, "metadata")
        workload_name = _scalar(_mapping_value(metadata, "name")) if isinstance(metadata, MappingNode) else None
        if template_path is None or workload_name is None:
            continue
        pod_spec = _nested_mapping(document, template_path)
        if pod_spec is None:
            continue
        for container_group in ("containers", "initContainers"):
            containers = _mapping_value(pod_spec, container_group)
            if not isinstance(containers, SequenceNode):
                continue
            for container in containers.value:
                if not isinstance(container, MappingNode):
                    continue
                container_name = _scalar(_mapping_value(container, "name"))
                environment = _mapping_value(container, "env")
                if container_name is None or not isinstance(environment, SequenceNode):
                    continue
                for item in environment.value:
                    binding = _environment_binding(item, workload_kind, workload_name, container_name, path)
                    if binding is not None:
                        bindings.append(binding)
    return bindings


def parse_kubernetes_configuration_sources_file(path: Path) -> list[KubernetesConfigurationSource]:
    """Return declared ConfigMap/Secret key names without parsing any values."""
    text = path.read_text()
    try:
        documents = list(yaml.compose_all(text))
    except yaml.YAMLError:
        return []
    sources: list[KubernetesConfigurationSource] = []
    for document in documents:
        if not isinstance(document, MappingNode):
            continue
        resource_kind = _scalar(_mapping_value(document, "kind"))
        source_kind = {"ConfigMap": "config_map", "Secret": "secret"}.get(resource_kind)
        metadata = _mapping_value(document, "metadata")
        source_name = _scalar(_mapping_value(metadata, "name")) if isinstance(metadata, MappingNode) else None
        if source_kind is None or source_name is None:
            continue
        keys = sorted({
            key
            for section in ("data", "stringData", "binaryData")
            if isinstance(values := _mapping_value(document, section), MappingNode)
            for key_node, _value_node in values.value
            if (key := _scalar(key_node)) is not None
        })
        sources.append(KubernetesConfigurationSource(
            source_kind=source_kind, source_name=source_name, keys=tuple(keys), file_path=str(path),
            start_line=document.start_mark.line + 1, end_line=document.end_mark.line + 1,
        ))
    return sources


def _environment_binding(
    item: Node, workload_kind: str, workload_name: str, container_name: str, path: Path,
) -> KubernetesConfigurationBinding | None:
    if not isinstance(item, MappingNode):
        return None
    environment_key = _scalar(_mapping_value(item, "name"))
    value_from = _mapping_value(item, "valueFrom")
    if environment_key is None or not isinstance(value_from, MappingNode):
        return None
    for source_kind, reference_name in (("config_map", "configMapKeyRef"), ("secret", "secretKeyRef")):
        reference = _mapping_value(value_from, reference_name)
        if not isinstance(reference, MappingNode):
            continue
        source_name = _scalar(_mapping_value(reference, "name"))
        source_key = _scalar(_mapping_value(reference, "key"))
        if source_name is None or source_key is None:
            return None
        return KubernetesConfigurationBinding(
            environment_key=environment_key, source_kind=source_kind, source_name=source_name,
            source_key=source_key, workload_kind=workload_kind, workload_name=workload_name,
            container_name=container_name, file_path=str(path), start_line=item.start_mark.line + 1,
            end_line=item.end_mark.line + 1,
        )
    return None


def _nested_mapping(node: MappingNode, keys: tuple[str, ...]) -> MappingNode | None:
    current: Node | None = node
    for key in keys:
        if not isinstance(current, MappingNode):
            return None
        current = _mapping_value(current, key)
    return current if isinstance(current, MappingNode) else None


def _mapping_value(node: MappingNode, key: str) -> Node | None:
    for key_node, value_node in node.value:
        if _scalar(key_node) == key:
            return value_node
    return None


def _scalar(node: Node | None) -> str | None:
    return node.value if isinstance(node, ScalarNode) and isinstance(node.value, str) and node.value else None
