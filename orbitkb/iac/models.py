"""Shared result shape for every IaC parser (Terraform, CloudFormation,
Kubernetes, compose) — one dataclass so each parser stays a thin translation
from its own format's structured parse into the same fact shape, instead of
each format inventing its own return type."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class IacResource:
    provider: str
    resource_type: str
    iac_resource_type: str
    logical_name: str
    physical_name: str | None
    source_format: str
    confidence: str
    file_path: str
    start_line: int
    end_line: int
    # Set only by orbitkb.iac.scanner, never by an individual format parser: the
    # indexed service whose root structurally contains file_path, when exactly
    # one candidate matches. None means "belongs to the repository, ownership
    # not resolvable" — never a guess between multiple candidates.
    matched_service_name: str | None = None
    # A small, curated set of literal attributes worth tracking for smells
    # (see cloud_taxonomy.IAC_PRESENCE_ATTRIBUTES/IAC_VALUE_ATTRIBUTES) — bool
    # for presence-only attributes, str for value-matters ones. Empty when the
    # resource type has none tracked, never a guessed/default-filled value.
    attributes: dict[str, bool | str] = field(default_factory=dict)
