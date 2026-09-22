"""Repository-wide IaC scan — the one place that knows the whole repository
tree, unlike a stack detector which only ever sees one service's own root.

IaC commonly lives outside any single service's folder (infra/, terraform/,
deploy/), so this walks `repository_root` once and attributes each found
resource to a service only by structural path containment, never a guess:
exactly one matching candidate attributes it, zero or more than one leaves it
repository-scoped.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from orbitkb.discovery.scan_helpers import SKIP_DIRS
from orbitkb.discovery.walker import ServiceCandidate
from orbitkb.iac.cloudformation import parse_cloudformation_file
from orbitkb.iac.compose import parse_compose_file
from orbitkb.iac.kubernetes import is_helm_template
from orbitkb.iac.models import IacResource
from orbitkb.iac.terraform import parse_terraform_file


def _is_skipped(path: Path, repository_root: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.relative_to(repository_root).parts)


def _is_compose_file(path: Path) -> bool:
    return path.name.startswith("docker-compose") and path.suffix in (".yml", ".yaml")


def _parse_file(path: Path) -> list[IacResource]:
    if path.suffix == ".tf":
        return parse_terraform_file(path)
    if _is_compose_file(path):
        return parse_compose_file(path)
    if path.suffix == ".json":
        return parse_cloudformation_file(path)
    if path.suffix in (".yaml", ".yml"):
        # A CloudFormation YAML template and a plain Kubernetes manifest share
        # the same extension; an unrendered Helm chart template additionally
        # uses Go template syntax that isn't valid YAML on its own. Checking
        # for that first avoids attempting to parse it as either.
        if is_helm_template(path, path.read_text()):
            return []
        # parse_cloudformation_file itself no-ops (returns []) on a document
        # with no top-level `Resources:` — which every plain Kubernetes
        # manifest is, since that's not something both schemas coincidentally
        # share — so no separate "is this CloudFormation" sniff is needed.
        return parse_cloudformation_file(path)
    return []


def _matching_service_name(file_path: str, candidates: list[ServiceCandidate]) -> str | None:
    resolved = Path(file_path).resolve()
    matches = [c.name for c in candidates if resolved.is_relative_to(c.path.resolve())]
    return matches[0] if len(matches) == 1 else None


def scan_repository(repository_root: Path, candidates: list[ServiceCandidate]) -> list[IacResource]:
    resources: list[IacResource] = []
    for path in repository_root.rglob("*"):
        if not path.is_file() or _is_skipped(path, repository_root):
            continue
        for resource in _parse_file(path):
            matched = _matching_service_name(resource.file_path, candidates)
            resources.append(replace(resource, matched_service_name=matched))
    return resources
