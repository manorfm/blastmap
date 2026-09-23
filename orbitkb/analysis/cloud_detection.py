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
    AWS_SDK_GO_V2_METHODS,
    AWS_SDK_JAVA_V1_FQN,
    AWS_SDK_JAVA_V1_TYPES,
    AWS_SDK_JAVA_V2_FQN,
    AWS_SDK_JAVA_V2_TYPES,
    AWS_SDK_JS_V3_COMMANDS,
    AWS_SDK_JS_V3_MODULE_SERVICE,
    AWS_SDK_METHOD_TABLE,
    AWS_SERVICE_RESOURCE_TYPE,
    AZURE_BLOB_CLIENT_TYPES,
    AZURE_BLOB_JAVA_FQN,
    AZURE_BLOB_METHOD_TABLE,
    BOTO3_SERVICE_LITERALS,
    GO_CLOUD_IMPORT_PATHS,
)
from orbitkb.analysis.go_imports import parse_go_import_paths
from orbitkb.analysis.jvm_imports import parse_jvm_imports
from orbitkb.analysis.models import CloudFact, Evidence
from orbitkb.analysis.node_imports import parse_node_named_imports

# (provider, service_name, resource_type, sdk, operation lookup table) — what a
# locally-declared client variable/parameter/field resolves to, shared by every
# "declared client -> method call" detector below (JVM, Go).
_ClientKind = tuple[str, str, str, str, "dict[str, tuple[str, str]]"]

_NODE_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx"}


def _line(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1


def _node_command_imports(source: str) -> dict[str, tuple[str, str]]:
    """Local identifier -> (module basename, original Command class name), for
    every named import from a recognized `@aws-sdk/client-*` package."""
    return {
        local_name: (module_name, original_name)
        for local_name, module_name, original_name in parse_node_named_imports(source)
        if module_name in AWS_SDK_JS_V3_MODULE_SERVICE and original_name in AWS_SDK_JS_V3_COMMANDS
    }


def _node_azure_import_names(source: str) -> set[str]:
    """Local identifiers proven imported from `@azure/storage-blob` — the
    same import-source check `_node_command_imports` already applies for AWS,
    closing the gap where a project's own unrelated class happening to be
    named `BlobServiceClient` would otherwise be mistaken for Azure's."""
    return {
        local_name
        for local_name, module_name, original_name in parse_node_named_imports(source)
        if module_name == "storage-blob" and original_name in AZURE_BLOB_CLIENT_TYPES
    }


_NODE_AZURE_BLOB_DECLARATION_RE = re.compile(
    r"\b(?:const|let|var)\s+(\w+)\s*=\s*new\s+("
    + "|".join(re.escape(t) for t in sorted(AZURE_BLOB_CLIENT_TYPES))
    + r")\s*\("
)


def _node_azure_client_declarations(source: str) -> dict[str, _ClientKind]:
    """Azure Blob's Node SDK is a stateful client bound to a variable (unlike
    AWS SDK v3's stateless Command construction), so this needs the same
    "declared client -> later method call" resolution JVM/Go already use, not
    the Command-construction shortcut `_node_cloud_facts` takes for AWS."""
    verified_types = _node_azure_import_names(source)
    return {
        identifier: ("azure", "blob_storage", "object_storage", "azure-storage-blob", AZURE_BLOB_METHOD_TABLE)
        for identifier, type_name in _NODE_AZURE_BLOB_DECLARATION_RE.findall(source)
        if type_name in verified_types
    }


def _node_cloud_facts(source: str, rel_path: str) -> list[CloudFact]:
    facts: list[CloudFact] = list(_client_call_facts(source, rel_path, _node_azure_client_declarations(source)))
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


def _client_call_facts(source: str, rel_path: str, declarations: dict[str, _ClientKind]) -> list[CloudFact]:
    """Shared by every "declared client -> method call" detector (JVM, Go): once
    a variable/parameter/field is known to be a recognized SDK client, any call
    on that identifier whose method name matches the client's own operation
    table is a fact — regardless of which language declared it."""
    facts: list[CloudFact] = []
    for identifier, (provider, service_name, resource_type, sdk, method_table) in declarations.items():
        pattern = re.compile(r"\b" + re.escape(identifier) + r"\s*\.\s*(\w+)\s*\(")
        for match in pattern.finditer(source):
            operation = method_table.get(match.group(1))
            if operation is None:
                continue
            operation_kind, canonical_operation = operation
            line = _line(source, match.start())
            facts.append(CloudFact(
                provider=provider, resource_type=resource_type, service_name=service_name,
                operation=canonical_operation, operation_kind=operation_kind, sdk=sdk, target_name=None,
                evidence=Evidence(file_path=rel_path, start_line=line, end_line=line),
            ))
    return facts


def _jvm_client_kind(type_name: str, imports: dict[str, str]) -> _ClientKind | None:
    """Only trusts `type_name` once its own import resolves to the exact FQN
    the real SDK ships — a project's own unrelated `SqsClient` with no such
    import (or a different one) resolves to None here, not a false positive."""
    resolved_fqn = imports.get(type_name)
    if resolved_fqn is None:
        return None
    if resolved_fqn == AWS_SDK_JAVA_V2_FQN.get(type_name):
        service_name = AWS_SDK_JAVA_V2_TYPES[type_name]
        resource_type = AWS_SERVICE_RESOURCE_TYPE.get(service_name)
        return None if resource_type is None else ("aws", service_name, resource_type, "aws-sdk-java-v2", AWS_SDK_METHOD_TABLE)
    if resolved_fqn == AWS_SDK_JAVA_V1_FQN.get(type_name):
        service_name = AWS_SDK_JAVA_V1_TYPES[type_name]
        resource_type = AWS_SERVICE_RESOURCE_TYPE.get(service_name)
        return None if resource_type is None else ("aws", service_name, resource_type, "aws-sdk-java-v1", AWS_SDK_METHOD_TABLE)
    if resolved_fqn == AZURE_BLOB_JAVA_FQN.get(type_name):
        return ("azure", "blob_storage", "object_storage", "azure-storage-blob", AZURE_BLOB_METHOD_TABLE)
    return None


_JVM_CLIENT_TYPE_ALTERNATION = "|".join(
    re.escape(t) for t in sorted({*AWS_SDK_JAVA_V1_TYPES, *AWS_SDK_JAVA_V2_TYPES, *AZURE_BLOB_CLIENT_TYPES}, key=len, reverse=True)
)
# Java: `TYPE name;` / Kotlin: `val name: TYPE` — declaration order is reversed
# between the two languages, so each gets its own pattern rather than one
# trying to cover both orders ambiguously.
_JAVA_FIELD_RE = re.compile(
    r"\b(?:private|protected|public)?\s*(?:final\s+)?(" + _JVM_CLIENT_TYPE_ALTERNATION + r")\s+(\w+)\s*[=;]"
)
_KOTLIN_FIELD_RE = re.compile(
    r"\b(?:private\s+|protected\s+|public\s+)?(?:val|var)\s+(\w+)\s*:\s*(" + _JVM_CLIENT_TYPE_ALTERNATION + r")\b"
)


def _jvm_client_declarations(source: str) -> dict[str, _ClientKind]:
    imports = parse_jvm_imports(source)
    declarations: dict[str, _ClientKind] = {}
    for match in _JAVA_FIELD_RE.finditer(source):
        kind = _jvm_client_kind(match.group(1), imports)
        if kind is not None:
            declarations[match.group(2)] = kind
    for match in _KOTLIN_FIELD_RE.finditer(source):
        kind = _jvm_client_kind(match.group(2), imports)
        if kind is not None:
            declarations[match.group(1)] = kind
    return declarations


def _jvm_cloud_facts(source: str, rel_path: str) -> list[CloudFact]:
    return _client_call_facts(source, rel_path, _jvm_client_declarations(source))


# Any package alias, not a fixed set — safety comes from verifying the
# alias's own import path against GO_CLOUD_IMPORT_PATHS below, not from
# constraining which alias spellings this regex will even consider.
_GO_CLIENT_PARAMETER_RE = re.compile(r"\b(\w+)\s+\*(\w+)\.Client\b")


def _go_client_declarations(source: str) -> dict[str, _ClientKind]:
    import_paths = parse_go_import_paths(source)
    declarations: dict[str, _ClientKind] = {}
    for identifier, package_alias in _GO_CLIENT_PARAMETER_RE.findall(source):
        resolved = GO_CLOUD_IMPORT_PATHS.get(import_paths.get(package_alias, ""))
        if resolved is None:
            continue
        provider, service_name = resolved
        if provider == "azure":
            declarations[identifier] = ("azure", "blob_storage", "object_storage", "azure-storage-blob", AZURE_BLOB_METHOD_TABLE)
            continue
        resource_type = AWS_SERVICE_RESOURCE_TYPE.get(service_name)
        if resource_type is not None:
            declarations[identifier] = ("aws", service_name, resource_type, "aws-sdk-go-v2", AWS_SDK_GO_V2_METHODS)
    return declarations


def _go_cloud_facts(source: str, rel_path: str) -> list[CloudFact]:
    return _client_call_facts(source, rel_path, _go_client_declarations(source))


def detect_cloud_facts(files: list[Path], root: Path) -> list[CloudFact]:
    facts: list[CloudFact] = []
    for path in files:
        rel_path = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        if path.suffix in _NODE_EXTENSIONS:
            facts.extend(_node_cloud_facts(text, rel_path))
        elif path.suffix == ".py":
            facts.extend(_python_cloud_facts(text, rel_path))
        elif path.suffix in {".java", ".kt"}:
            facts.extend(_jvm_cloud_facts(text, rel_path))
        elif path.suffix == ".go":
            facts.extend(_go_cloud_facts(text, rel_path))
    return facts
