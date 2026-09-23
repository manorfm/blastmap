"""Terraform (`.tf`) parsing via `python-hcl2` — a real HCL2 grammar, never
regex/keyword heuristics. A resource's type and logical name are HCL2 syntax
positions that can never be an interpolated expression, so once parsed they are
ground truth; only an *attribute value* (like `name`) may be dynamic, and that
case is stored as unresolved rather than guessed.

`hcl2.load()` keeps a quoted string's literal surrounding quote characters in
the returned value (e.g. the Python string `'"orders-queue"'`, not
`'orders-queue'`) — `_unquote` below is exactly that normalization, nothing
more.

AWS provider v4+ split S3 bucket versioning/encryption out of `aws_s3_bucket`
into their own resources (`aws_s3_bucket_versioning`,
`aws_s3_bucket_server_side_encryption_configuration`), each pointing back at
its bucket via a reference expression (`bucket = aws_s3_bucket.orders.id`),
which `hcl2.load()` represents as the literal string `"${aws_s3_bucket.orders.id}"`.
Neither settings resource is a cloud resource of its own — modeling either as
a separate `cloud_iac_resources` row would be misleading — so
`_referenced_bucket_names` below correlates them back to their bucket's own
declaration at parse time, folding the result into that bucket's
`attributes` dict as `versioning_configured`/`encryption_configured`.
"""
from __future__ import annotations

import re
from pathlib import Path

import hcl2
from lark.exceptions import LarkError

from orbitkb.analysis.cloud_taxonomy import (
    IAC_NAME_ATTRIBUTES,
    IAC_PRESENCE_ATTRIBUTES,
    IAC_RESOURCE_TYPE_TABLE,
    IAC_VALUE_ATTRIBUTES,
    is_unresolved_literal,
)
from orbitkb.iac.evidence import find_line
from orbitkb.iac.models import IacResource


def _unquote(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == '"' and stripped[-1] == '"':
        return stripped[1:-1]
    return stripped


def _resolve_physical_name(attrs: dict, iac_resource_type: str) -> str | None:
    for attr_name in IAC_NAME_ATTRIBUTES.get(iac_resource_type, ()):
        value = _unquote(attrs.get(attr_name))
        if value is None or is_unresolved_literal(value):
            continue
        return value
    return None


def _resolve_attributes(attrs: dict, iac_resource_type: str) -> dict[str, bool | str]:
    """Presence-only and value-matters tracked attributes for one resource,
    same "only literal" posture as _resolve_physical_name for the latter."""
    resolved: dict[str, bool | str] = {}
    for attr_name in IAC_PRESENCE_ATTRIBUTES.get(iac_resource_type, ()):
        if attr_name in attrs:
            resolved[attr_name] = True
    for attr_name in IAC_VALUE_ATTRIBUTES.get(iac_resource_type, ()):
        value = _unquote(attrs.get(attr_name))
        if value is not None and not is_unresolved_literal(value):
            resolved[attr_name] = value
    return resolved


def _declaration_pattern(iac_resource_type: str, logical_name: str) -> re.Pattern[str]:
    return re.compile(
        r'resource\s+"' + re.escape(iac_resource_type) + r'"\s+"' + re.escape(logical_name) + r'"'
    )


_BUCKET_REFERENCE_RE = re.compile(r"^\$\{aws_s3_bucket\.(\w+)\.")
_VERSIONING_RESOURCE_TYPE = "aws_s3_bucket_versioning"
_ENCRYPTION_RESOURCE_TYPE = "aws_s3_bucket_server_side_encryption_configuration"


def _referenced_bucket_names(raw_resources: list[dict], target_iac_type: str) -> set[str]:
    """Logical names of every `aws_s3_bucket` a `target_iac_type` resource
    points its own `bucket` attribute at — see this module's docstring."""
    names: set[str] = set()
    for block in raw_resources:
        for raw_type, named in block.items():
            if _unquote(raw_type) != target_iac_type:
                continue
            for attrs in named.values():
                bucket_ref = attrs.get("bucket")
                if isinstance(bucket_ref, str) and (match := _BUCKET_REFERENCE_RE.match(bucket_ref)):
                    names.add(match.group(1))
    return names


def parse_terraform_file(path: Path) -> list[IacResource]:
    """One `IacResource` per declared resource whose type is in
    `IAC_RESOURCE_TYPE_TABLE`; every other resource type is silently skipped —
    never guessed at. A malformed `.tf` file yields no resources rather than
    failing the whole scan over one bad file."""
    text = path.read_text()
    try:
        with path.open() as handle:
            parsed = hcl2.load(handle)
    except LarkError:
        return []

    raw_resources = parsed.get("resource", [])
    versioned_buckets = _referenced_bucket_names(raw_resources, _VERSIONING_RESOURCE_TYPE)
    encrypted_buckets = _referenced_bucket_names(raw_resources, _ENCRYPTION_RESOURCE_TYPE)

    resources: list[IacResource] = []
    for block in raw_resources:
        for raw_type, named in block.items():
            iac_resource_type = _unquote(raw_type)
            mapping = IAC_RESOURCE_TYPE_TABLE.get(iac_resource_type)
            if mapping is None:
                continue
            provider, resource_type, _service_name = mapping
            for raw_name, attrs in named.items():
                logical_name = _unquote(raw_name)
                physical_name = _resolve_physical_name(attrs, iac_resource_type)
                line = find_line(text, _declaration_pattern(iac_resource_type, logical_name))
                resolved_attributes = _resolve_attributes(attrs, iac_resource_type)
                if iac_resource_type == "aws_s3_bucket":
                    if logical_name in versioned_buckets:
                        resolved_attributes["versioning_configured"] = True
                    if logical_name in encrypted_buckets:
                        resolved_attributes["encryption_configured"] = True
                resources.append(IacResource(
                    provider=provider,
                    resource_type=resource_type,
                    iac_resource_type=iac_resource_type,
                    logical_name=logical_name,
                    physical_name=physical_name,
                    source_format="terraform",
                    confidence="high",
                    file_path=str(path),
                    start_line=line,
                    end_line=line,
                    attributes=resolved_attributes,
                ))
    return resources
