"""Shared result shape for every IaC parser (Terraform, CloudFormation,
Kubernetes, compose) — one dataclass so each parser stays a thin translation
from its own format's structured parse into the same fact shape, instead of
each format inventing its own return type."""
from __future__ import annotations

from dataclasses import dataclass


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
