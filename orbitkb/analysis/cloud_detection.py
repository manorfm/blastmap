"""Deterministic cloud SDK operation detection, computed once across every
file — the same shape as `PersistenceFact` detection in `engine.py`
(`_persistence_facts`, called once from `StaticAnalysisEngine.analyze`): a
flat pass over raw source text, not integrated into each language's
tree-sitter `_FileAnalyzer`. What each pass matches is still a real syntactic
anchor from `orbitkb.analysis.cloud_taxonomy` — an imported SDK class actually
constructed, or a client bound from a literal service-name argument — never a
loose keyword guess.

Trade-off accepted for v1 (surfaced to and confirmed by the user): a
`CloudFact` here does not also produce a `FlowEdge`, so it will not appear in
`trace_flow` or `describe_entrypoint`'s bounded flow — only in
`describe_cloud_dependencies`. Wiring cloud operations into per-language
bounded-flow resolution the way GORM/Mongoose calls are is a legitimate
follow-up, not required for the deterministic fact itself.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from orbitkb.analysis.cloud_taxonomy import (
    AWS_SDK_JS_V3_COMMANDS,
    AWS_SDK_JS_V3_MODULE_SERVICE,
    AWS_SDK_METHOD_TABLE,
    AWS_SERVICE_RESOURCE_TYPE,
    BOTO3_SERVICE_LITERALS,
)
from orbitkb.analysis.models import CloudFact, Evidence

_NODE_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx"}
_NODE_IMPORT_RE = re.compile(r"import\s*\{([^}]+)\}\s*from\s*[\"']([^\"']+)[\"']")


def _line(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1


def _node_command_imports(source: str) -> dict[str, tuple[str, str]]:
    """Local identifier -> (module basename, original Command class name), for
    every named import from a recognized `@aws-sdk/client-*` package. Mirrors
    `_node_named_imports` in engine.py, scoped down to only the imports this
    detector cares about."""
    mapping: dict[str, tuple[str, str]] = {}
    for names, module in _NODE_IMPORT_RE.findall(source):
        module_name = Path(module).name
        if module_name not in AWS_SDK_JS_V3_MODULE_SERVICE:
            continue
        for item in names.split(","):
            original, _as, local = item.strip().partition(" as ")
            original = original.strip()
            if original in AWS_SDK_JS_V3_COMMANDS:
                mapping[(local or original).strip()] = (module_name, original)
    return mapping


def _node_cloud_facts(source: str, rel_path: str) -> list[CloudFact]:
    facts: list[CloudFact] = []
    for local_name, (module_name, original) in _node_command_imports(source).items():
        service_name = AWS_SDK_JS_V3_MODULE_SERVICE[module_name]
        resource_type = AWS_SERVICE_RESOURCE_TYPE.get(service_name)
        if resource_type is None:
            continue
        operation_kind, operation = AWS_SDK_JS_V3_COMMANDS[original]
        pattern = re.compile(r"\bnew\s+" + re.escape(local_name) + r"\s*\(")
        for match in pattern.finditer(source):
            line = _line(source, match.start())
            facts.append(CloudFact(
                provider="aws", resource_type=resource_type, service_name=service_name,
                operation=operation, operation_kind=operation_kind, sdk="aws-sdk-js-v3",
                target_name=None,
                evidence=Evidence(file_path=rel_path, start_line=line, end_line=line),
            ))
    return facts


def _boto3_bound_variables(tree: ast.Module) -> dict[str, str]:
    """Local variable name -> service_name, for every `boto3.client(<literal>)`
    / `boto3.resource(<literal>)` assignment. The literal service string
    passed to boto3 is itself the proof of which AWS service it talks to."""
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not (
            isinstance(call.func, ast.Attribute)
            and call.func.attr in {"client", "resource"}
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "boto3"
            and call.args
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
        ):
            continue
        service_name = BOTO3_SERVICE_LITERALS.get(call.args[0].value)
        if service_name is None:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                bound[target.id] = service_name
    return bound


def _python_cloud_facts(source: str, rel_path: str) -> list[CloudFact]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    bound_services = _boto3_bound_variables(tree)
    facts: list[CloudFact] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in bound_services
            and node.func.attr in AWS_SDK_METHOD_TABLE
        ):
            continue
        service_name = bound_services[node.func.value.id]
        resource_type = AWS_SERVICE_RESOURCE_TYPE.get(service_name)
        if resource_type is None:
            continue
        operation_kind, operation = AWS_SDK_METHOD_TABLE[node.func.attr]
        end_line = getattr(node, "end_lineno", node.lineno) or node.lineno
        facts.append(CloudFact(
            provider="aws", resource_type=resource_type, service_name=service_name,
            operation=operation, operation_kind=operation_kind, sdk="boto3",
            target_name=None,
            evidence=Evidence(file_path=rel_path, start_line=node.lineno, end_line=end_line),
        ))
    return facts


def detect_cloud_facts(files: list[Path], root: Path) -> list[CloudFact]:
    facts: list[CloudFact] = []
    for path in files:
        rel_path = path.relative_to(root).as_posix()
        if path.suffix in _NODE_EXTENSIONS:
            facts.extend(_node_cloud_facts(path.read_text(encoding="utf-8", errors="ignore"), rel_path))
        elif path.suffix == ".py":
            facts.extend(_python_cloud_facts(path.read_text(encoding="utf-8", errors="ignore"), rel_path))
    return facts
