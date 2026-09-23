"""CloudFormation (JSON/YAML) template parsing. JSON is stdlib `json.loads`;
YAML goes through `cfn-flip`'s `cfn_tools.load_yaml`, the AWS-community tool
`cfn-lint`/`taskcat` already use to handle CloudFormation's short-form
intrinsic tags (`!Ref`, `!GetAtt`, `!Sub`, ...) — those aren't valid plain YAML
on their own, so hand-rolled PyYAML constructors would mean reinventing the
same ~10 tags this library already covers.

A resource's `Type` is a literal string in the template — ground truth once
parsed. An intrinsic function used as a property value (`{"Fn::Sub": ...}`)
means that value depends on stack context CloudFormation resolves at deploy
time, never locally — the property stays unresolved rather than guessed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import cfn_tools
import yaml

from orbitkb.analysis.cloud_taxonomy import (
    IAC_NAME_ATTRIBUTES,
    IAC_PRESENCE_ATTRIBUTES,
    IAC_RESOURCE_TYPE_TABLE,
    IAC_VALUE_ATTRIBUTES,
)
from orbitkb.iac.evidence import find_line
from orbitkb.iac.models import IacResource


def _load_template(path: Path) -> dict | None:
    text = path.read_text()
    try:
        if path.suffix == ".json":
            return json.loads(text)
        return cfn_tools.load_yaml(text)
    except (json.JSONDecodeError, yaml.YAMLError):
        return None


def _resolve_physical_name(properties: dict, iac_resource_type: str) -> str | None:
    for attr_name in IAC_NAME_ATTRIBUTES.get(iac_resource_type, ()):
        value = properties.get(attr_name)
        if isinstance(value, str):
            return value
        # A dict here is an intrinsic function (Fn::Sub, Fn::Join, Ref, ...):
        # its real value depends on stack context resolved only at deploy time.
    return None


def _resolve_attributes(properties: dict, iac_resource_type: str) -> dict[str, bool | str]:
    """Presence-only and value-matters tracked attributes for one resource,
    same "only literal, an intrinsic function stays unresolved" posture as
    _resolve_physical_name for the latter."""
    resolved: dict[str, bool | str] = {}
    for attr_name in IAC_PRESENCE_ATTRIBUTES.get(iac_resource_type, ()):
        if attr_name in properties:
            resolved[attr_name] = True
    for attr_name in IAC_VALUE_ATTRIBUTES.get(iac_resource_type, ()):
        value = properties.get(attr_name)
        if isinstance(value, str):
            resolved[attr_name] = value
    return resolved


def _declaration_pattern(logical_name: str) -> re.Pattern[str]:
    return re.compile(re.escape(logical_name) + r"\s*:")


def parse_cloudformation_file(path: Path) -> list[IacResource]:
    """One `IacResource` per declared resource whose `Type` is in
    `IAC_RESOURCE_TYPE_TABLE`; every other type is silently skipped. A
    malformed or resource-less template yields no resources rather than
    failing the whole scan."""
    template = _load_template(path)
    if not isinstance(template, dict):
        return []
    declared = template.get("Resources")
    if not isinstance(declared, dict):
        return []

    text = path.read_text()
    resources: list[IacResource] = []
    for logical_name, body in declared.items():
        if not isinstance(body, dict):
            continue
        iac_resource_type = body.get("Type")
        mapping = IAC_RESOURCE_TYPE_TABLE.get(iac_resource_type)
        if mapping is None:
            continue
        provider, resource_type, _service_name = mapping
        properties = body.get("Properties") or {}
        physical_name = _resolve_physical_name(properties, iac_resource_type)
        line = find_line(text, _declaration_pattern(logical_name))
        resources.append(IacResource(
            provider=provider,
            resource_type=resource_type,
            iac_resource_type=iac_resource_type,
            logical_name=logical_name,
            physical_name=physical_name,
            source_format="cloudformation",
            confidence="high",
            file_path=str(path),
            start_line=line,
            end_line=line,
            attributes=_resolve_attributes(properties, iac_resource_type),
        ))
    return resources
