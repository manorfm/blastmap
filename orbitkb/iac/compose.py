"""docker-compose `image:` scanning — a deliberately weak, explicitly
low-confidence signal that local development emulates certain cloud resources
(LocalStack, Azurite), never conflated with a real Terraform/CloudFormation
declaration. compose is dev-environment topology, not a resource-declaration
schema, so this only trusts two literal signals: a recognized emulator image,
and — for LocalStack, which emulates many AWS services at once — its own
`SERVICES` environment variable naming exactly which ones. Without that literal
list, which services LocalStack emulates isn't knowable without guessing, so
nothing is emitted rather than assuming all of them.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from orbitkb.analysis.cloud_taxonomy import (
    AWS_SERVICE_RESOURCE_TYPE,
    BOTO3_SERVICE_LITERALS,
)
from orbitkb.iac.evidence import find_line
from orbitkb.iac.models import IacResource

_LOCALSTACK_IMAGE = "localstack/localstack"
_AZURITE_IMAGE = "mcr.microsoft.com/azure-storage/azurite"


def _env_value(service: dict, key: str) -> str | None:
    env = service.get("environment")
    if isinstance(env, dict):
        value = env.get(key)
        return str(value) if value is not None else None
    if isinstance(env, list):
        for item in env:
            if isinstance(item, str) and item.startswith(f"{key}="):
                return item.split("=", 1)[1]
    return None


def _localstack_resources(
    logical_name: str, service: dict, file_path: str, line: int,
) -> list[IacResource]:
    services_literal = _env_value(service, "SERVICES")
    if not services_literal:
        return []
    resources = []
    for raw in services_literal.split(","):
        service_name = BOTO3_SERVICE_LITERALS.get(raw.strip())
        resource_type = AWS_SERVICE_RESOURCE_TYPE.get(service_name) if service_name else None
        if resource_type is None:
            continue
        resources.append(IacResource(
            provider="aws",
            resource_type=resource_type,
            iac_resource_type="localstack",
            logical_name=logical_name,
            physical_name=None,
            source_format="compose_hint",
            confidence="low",
            file_path=file_path,
            start_line=line,
            end_line=line,
        ))
    return resources


def _azurite_resource(logical_name: str, file_path: str, line: int) -> IacResource:
    return IacResource(
        provider="azure",
        resource_type="object_storage",
        iac_resource_type="azurite",
        logical_name=logical_name,
        physical_name=None,
        source_format="compose_hint",
        confidence="low",
        file_path=file_path,
        start_line=line,
        end_line=line,
    )


def parse_compose_file(path: Path) -> list[IacResource]:
    text = path.read_text()
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return []
    if not isinstance(parsed, dict):
        return []
    services = parsed.get("services")
    if not isinstance(services, dict):
        return []

    resources: list[IacResource] = []
    for logical_name, service in services.items():
        if not isinstance(service, dict):
            continue
        image = service.get("image")
        if image == _LOCALSTACK_IMAGE:
            line = find_line(text, re.compile(re.escape(_LOCALSTACK_IMAGE)))
            resources.extend(_localstack_resources(logical_name, service, str(path), line))
        elif image == _AZURITE_IMAGE:
            line = find_line(text, re.compile(re.escape(_AZURITE_IMAGE)))
            resources.append(_azurite_resource(logical_name, str(path), line))
    return resources
