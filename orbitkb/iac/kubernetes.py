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

_TEMPLATE_MARKER = "{{"


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
