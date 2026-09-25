"""Repository-wide IaC scan — the one place that knows the whole repository
tree, unlike a stack detector which only ever sees one service's own root.

IaC commonly lives outside any single service's folder (infra/, terraform/,
deploy/), so this walks `repository_root` once and attributes each found
resource to a service only by structural path containment, never a guess:
exactly one matching candidate attributes it, zero or more than one leaves it
repository-scoped.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from orbitkb.discovery.scan_helpers import SKIP_DIRS
from orbitkb.discovery.walker import ServiceCandidate
from orbitkb.iac.cloudformation import parse_cloudformation_file
from orbitkb.iac.compose import parse_compose_file
from orbitkb.iac.kubernetes import (
    is_helm_template,
    parse_kubernetes_configuration_references_file,
    parse_kubernetes_configuration_sources_file,
)
from orbitkb.iac.models import (
    IacResource,
    KubernetesConfigurationBinding,
    KubernetesConfigurationKeyMismatch,
    KubernetesConfigurationSource,
    KubernetesConfigurationSourceImport,
    KubernetesConfigurationSourceImportUnknown,
    KubernetesConfigurationSourceUnknown,
)
from orbitkb.iac.terraform import parse_terraform_file


def _is_skipped(path: Path, repository_root: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.relative_to(repository_root).parts)


def _is_compose_file(path: Path) -> bool:
    return path.name.startswith("docker-compose") and path.suffix in (".yml", ".yaml")


@dataclass(frozen=True)
class RepositoryIacFacts:
    """Structurally parsed repository-wide infrastructure facts."""

    resources: list[IacResource]
    configuration_bindings: list[KubernetesConfigurationBinding]
    configuration_source_imports: list[KubernetesConfigurationSourceImport]
    configuration_source_import_unknowns: list[KubernetesConfigurationSourceImportUnknown]
    configuration_sources: list[KubernetesConfigurationSource]
    configuration_key_mismatches: list[KubernetesConfigurationKeyMismatch]
    configuration_source_unknowns: list[KubernetesConfigurationSourceUnknown]


def _parse_file(path: Path) -> RepositoryIacFacts:
    if path.suffix == ".tf":
        return RepositoryIacFacts(parse_terraform_file(path), [], [], [], [], [], [])
    if _is_compose_file(path):
        return RepositoryIacFacts(parse_compose_file(path), [], [], [], [], [], [])
    if path.suffix == ".json":
        return RepositoryIacFacts(parse_cloudformation_file(path), [], [], [], [], [], [])
    if path.suffix in (".yaml", ".yml"):
        # A CloudFormation YAML template and a plain Kubernetes manifest share
        # the same extension; an unrendered Helm chart template additionally
        # uses Go template syntax that isn't valid YAML on its own. Checking
        # for that first avoids attempting to parse it as either.
        if is_helm_template(path, path.read_text()):
            return RepositoryIacFacts([], [], [], [], [], [], [])
        # parse_cloudformation_file itself no-ops (returns []) on a document
        # with no top-level `Resources:` — which every plain Kubernetes
        # manifest is, since that's not something both schemas coincidentally
        # share — so no separate "is this CloudFormation" sniff is needed.
        bindings, source_imports = parse_kubernetes_configuration_references_file(path)
        return RepositoryIacFacts(
            parse_cloudformation_file(path), bindings, source_imports, [],
            parse_kubernetes_configuration_sources_file(path), [], [],
        )
    return RepositoryIacFacts([], [], [], [], [], [], [])


def _matching_service_name(file_path: str, candidates: list[ServiceCandidate]) -> str | None:
    resolved = Path(file_path).resolve()
    matches = [c.name for c in candidates if resolved.is_relative_to(c.path.resolve())]
    return matches[0] if len(matches) == 1 else None


def scan_repository_facts(repository_root: Path, candidates: list[ServiceCandidate]) -> RepositoryIacFacts:
    """Scan IaC once and attribute only structurally-contained service facts."""
    resources: list[IacResource] = []
    configuration_bindings: list[KubernetesConfigurationBinding] = []
    configuration_source_imports: list[KubernetesConfigurationSourceImport] = []
    configuration_sources: list[KubernetesConfigurationSource] = []
    for path in repository_root.rglob("*"):
        if not path.is_file() or _is_skipped(path, repository_root):
            continue
        facts = _parse_file(path)
        for resource in facts.resources:
            matched = _matching_service_name(resource.file_path, candidates)
            resources.append(replace(resource, matched_service_name=matched))
        for binding in facts.configuration_bindings:
            matched = _matching_service_name(binding.file_path, candidates)
            configuration_bindings.append(replace(binding, matched_service_name=matched))
        for source_import in facts.configuration_source_imports:
            matched = _matching_service_name(source_import.file_path, candidates)
            configuration_source_imports.append(replace(source_import, matched_service_name=matched))
        configuration_sources.extend(facts.configuration_sources)
    source_by_identity: dict[tuple[str, str], list[KubernetesConfigurationSource]] = {}
    for source in configuration_sources:
        source_by_identity.setdefault((source.source_kind, source.source_name), []).append(source)
    mismatches: list[KubernetesConfigurationKeyMismatch] = []
    source_unknowns: list[KubernetesConfigurationSourceUnknown] = []
    source_import_unknowns: list[KubernetesConfigurationSourceImportUnknown] = []
    for binding in configuration_bindings:
        matching_sources = source_by_identity.get((binding.source_kind, binding.source_name), [])
        if not matching_sources:
            source_unknowns.append(KubernetesConfigurationSourceUnknown(
                environment_key=binding.environment_key, source_kind=binding.source_kind, source_name=binding.source_name,
                source_key=binding.source_key, reference_file_path=binding.file_path,
                reference_start_line=binding.start_line, reference_end_line=binding.end_line,
                matched_service_name=binding.matched_service_name,
            ))
            continue
        if len(matching_sources) != 1 or binding.source_key in matching_sources[0].keys:
            continue
        source = matching_sources[0]
        mismatches.append(KubernetesConfigurationKeyMismatch(
            environment_key=binding.environment_key, source_kind=binding.source_kind, source_name=binding.source_name,
            source_key=binding.source_key, reference_file_path=binding.file_path,
            reference_start_line=binding.start_line, reference_end_line=binding.end_line,
            declaration_file_path=source.file_path, declaration_start_line=source.start_line,
            declaration_end_line=source.end_line, matched_service_name=binding.matched_service_name,
        ))
    for source_import in configuration_source_imports:
        if source_by_identity.get((source_import.source_kind, source_import.source_name)):
            continue
        source_import_unknowns.append(KubernetesConfigurationSourceImportUnknown(
            source_kind=source_import.source_kind, source_name=source_import.source_name, prefix=source_import.prefix,
            reference_file_path=source_import.file_path, reference_start_line=source_import.start_line,
            reference_end_line=source_import.end_line, workload_kind=source_import.workload_kind,
            workload_name=source_import.workload_name, container_name=source_import.container_name,
            container_role=source_import.container_role,
            optional=source_import.optional,
            matched_service_name=source_import.matched_service_name,
        ))
    return RepositoryIacFacts(
        resources, configuration_bindings, configuration_source_imports, source_import_unknowns,
        configuration_sources, mismatches, source_unknowns,
    )


def scan_repository(repository_root: Path, candidates: list[ServiceCandidate]) -> list[IacResource]:
    """Compatibility wrapper returning cloud-resource declarations only."""
    return scan_repository_facts(repository_root, candidates).resources
