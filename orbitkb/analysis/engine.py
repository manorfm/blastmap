"""Tree-sitter based, transport-aware static analysis.

This module deliberately produces a small flow model, not a generic code graph.
It parses source locally (zero LLM tokens) and records only entrypoints plus the
calls, persistence operations and messages reachable from their declared handler.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import tree_sitter_go
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_kotlin
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from orbitkb.analysis.depth import DepthProvider, NoopDepthProvider
from orbitkb.analysis.models import (
    AnalysisResult,
    EntryPoint,
    Evidence,
    FlowEdge,
    Injection,
    Symbol,
)
from orbitkb.analysis.resolution import BoundedFlowResolver
from orbitkb.discovery.scan_helpers import SKIP_DIRS


def _walk(node: Node):
    yield node
    for child in node.named_children:
        yield from _walk(child)


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")


def _string(node: Node, source: bytes) -> str | None:
    value = _text(node, source).strip()
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    return None


def _evidence(path: Path, root: Path, node: Node) -> Evidence:
    return Evidence(path.relative_to(root).as_posix(), node.start_point.row + 1, node.end_point.row + 1)


def _symbol(
    function: _Function,
    path: Path,
    root: Path,
    implements: tuple[str, ...] = (),
    imports: tuple[tuple[str, str], ...] = (),
    qualifiers: tuple[str, ...] = (),
    primary: bool = False,
) -> Symbol:
    owner, _separator, member = function.symbol.rpartition(".")
    return Symbol(
        function.symbol, owner, member or function.name, _evidence(path, root, function.declaration), implements, imports,
        qualifiers, primary,
    )


def _call_kind(target: str) -> str:
    name = target.lower()
    if any(word in name for word in ("publish", "produce", "sendmessage", "writemessages", "basicpublish")):
        return "publishes"
    if any(word in name for word in ("consume", "subscribe", "receive", "basicconsume")):
        return "consumes"
    receiver = name.rsplit(".", 1)[0] if "." in name else ""
    storage_receiver = any(word in receiver for word in (
        "repo", "repository", "dao", "database", "collection", "model", ".db", "store",
    ))
    if storage_receiver and any(word in name for word in ("save", "insert", "update", "delete", "create", "persist")):
        return "writes"
    if storage_receiver and any(word in name for word in ("find", "get", "query", "select", "load", "read")):
        return "reads"
    if any(word in name for word in ("validate", "authorize", "authenticate", "check")):
        return "validates"
    return "invokes"


@dataclass(frozen=True)
class _Function:
    name: str
    symbol: str
    body: Node
    declaration: Node


class _FileAnalyzer:
    def __init__(self, language: Language):
        self._parser = Parser(language)

    def parse(self, source: bytes) -> Node:
        return self._parser.parse(source).root_node

    @staticmethod
    def _edges_for(function: _Function, path: Path, root: Path, source: bytes) -> list[FlowEdge]:
        edges = []
        for node in _walk(function.body):
            if node.type not in {"call_expression", "method_invocation"}:
                continue
            # Grammar field names differ (Kotlin exposes the callee as the first
            # named child while Go/TypeScript call it `function`). Normalize that
            # syntax detail at the parser boundary.
            if node.type == "method_invocation":
                target = ".".join(
                    _text(child, source)
                    for child in node.named_children
                    if child.type in {"identifier", "type_identifier"}
                )
            else:
                callee = node.child_by_field_name("function") or (node.named_children[0] if node.named_children else None)
                if callee is None:
                    continue
                target = _text(callee, source)
            if not target:
                continue
            edges.append(FlowEdge(function.symbol, target, _call_kind(target), _evidence(path, root, node)))
        return edges


class _GoAnalyzer(_FileAnalyzer):
    ROUTE_METHODS: ClassVar[frozenset[str]] = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})

    def analyze(self, path: Path, root: Path) -> AnalysisResult:
        source = path.read_bytes()
        tree = self.parse(source)
        source_text = source.decode("utf-8", errors="ignore")
        package = _go_package_name(source_text, path)
        imports = _go_imports(source_text)
        functions: list[_Function] = []
        for node in _walk(tree):
            if node.type not in {"function_declaration", "method_declaration"}:
                continue
            name_node = node.child_by_field_name("name")
            body = node.child_by_field_name("body")
            if name_node is None or body is None:
                continue
            name = _text(name_node, source)
            receiver = ""
            if node.type == "method_declaration":
                receiver_node = node.child_by_field_name("receiver")
                receiver = _text(receiver_node, source) if receiver_node else ""
                receiver_match = re.search(r"\*?([A-Z][A-Za-z0-9_]*)", receiver)
                receiver = receiver_match.group(1) if receiver_match else ""
            symbol = f"{receiver}.{name}" if receiver else f"{package}.{name}"
            functions.append(_Function(name, symbol, body, node))

        result = AnalysisResult(
            edges=[edge for fn in functions for edge in self._edges_for(fn, path, root, source)],
            symbols=[_symbol(fn, path, root, imports=imports) for fn in functions],
        )
        by_last_name = {fn.name: fn for fn in functions}
        for node in _walk(tree):
            if node.type != "call_expression":
                continue
            callee = node.child_by_field_name("function")
            arguments = node.child_by_field_name("arguments")
            if callee is None or arguments is None:
                continue
            callee_text = _text(callee, source)
            method = callee_text.rsplit(".", 1)[-1].upper()
            args = arguments.named_children
            if method not in self.ROUTE_METHODS or len(args) < 2:
                continue
            route = _string(args[0], source)
            handler = _text(args[-1], source).rsplit(".", 1)[-1]
            function = by_last_name.get(handler)
            if route and function:
                result.entrypoints.append(EntryPoint("http", method, route, function.symbol, _evidence(path, root, node)))
                result.contracts[function.symbol] = _go_http_contract(_text(function.declaration, source))
        return result


class _KotlinSpringAnalyzer(_FileAnalyzer):
    ROUTES: ClassVar[dict[str, str]] = {
        "GetMapping": "GET",
        "PostMapping": "POST",
        "PutMapping": "PUT",
        "PatchMapping": "PATCH",
        "DeleteMapping": "DELETE",
    }

    def analyze(self, path: Path, root: Path) -> AnalysisResult:
        source = path.read_bytes()
        tree = self.parse(source)
        result = AnalysisResult()
        for class_node in (node for node in _walk(tree) if node.type == "class_declaration"):
            class_name_node = class_node.child_by_field_name("name")
            class_name = _text(class_name_node, source) if class_name_node else path.stem
            implements = _kotlin_supertypes(_text(class_node, source))
            annotations = _class_annotations(class_node, source)
            qualifiers = _qualifiers(annotations)
            primary = "@Primary" in annotations
            for parameter in (node for node in _walk(class_node) if node.type == "class_parameter"):
                types = [node for node in _walk(parameter) if node.type == "user_type"]
                if types:
                    name_match = re.search(r"(?:val|var)\s+(\w+)", _text(parameter, source))
                    injection_symbol = f"{class_name}.{name_match.group(1)}" if name_match else class_name
                    contract = _text(types[-1], source)
                    evidence = _evidence(path, root, parameter)
                    result.edges.append(FlowEdge(injection_symbol, contract, "injects", evidence))
                    result.injections.append(Injection(injection_symbol, contract, _first_qualifier(_text(parameter, source)), evidence))
            for function_node in (node for node in _walk(class_node) if node.type == "function_declaration"):
                name_node = function_node.child_by_field_name("name")
                if name_node is None:
                    continue
                symbol = f"{class_name}.{_text(name_node, source)}"
                body = function_node.child_by_field_name("body") or function_node
                function = _Function(_text(name_node, source), symbol, body, function_node)
                result.symbols.append(_symbol(function, path, root, implements, qualifiers=qualifiers, primary=primary))
                result.edges.extend(self._edges_for(function, path, root, source))
                modifiers = next((node for node in function_node.named_children if node.type == "modifiers"), None)
                modifier_text = _text(modifiers, source) if modifiers else ""
                match = re.search(r"@(GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping)\s*\(\s*\"([^\"]+)\"", modifier_text)
                if match:
                    result.entrypoints.append(EntryPoint("http", self.ROUTES[match.group(1)], match.group(2), symbol, _evidence(path, root, function_node)))
                    result.contracts[symbol] = _spring_http_contract(_text(function_node, source), modifier_text, kotlin=True)
                listener = re.search(r"@RabbitListener\s*\([^)]*\[\s*\"([^\"]+)\"", modifier_text)
                if listener:
                    result.entrypoints.append(EntryPoint("message", "CONSUME", listener.group(1), symbol, _evidence(path, root, function_node)))
        return result


class _JavaSpringAnalyzer(_FileAnalyzer):
    ROUTES = _KotlinSpringAnalyzer.ROUTES

    def analyze(self, path: Path, root: Path) -> AnalysisResult:
        source = path.read_bytes()
        tree = self.parse(source)
        result = AnalysisResult()
        for class_node in (node for node in _walk(tree) if node.type == "class_declaration"):
            class_name_node = class_node.child_by_field_name("name")
            class_name = _text(class_name_node, source) if class_name_node else path.stem
            implements = _java_interfaces(_text(class_node, source))
            annotations = _class_annotations(class_node, source)
            qualifiers = _qualifiers(annotations)
            primary = "@Primary" in annotations
            for field in (node for node in _walk(class_node) if node.type == "field_declaration"):
                types = [node for node in _walk(field) if node.type == "type_identifier"]
                names = [node for node in _walk(field) if node.type == "variable_declarator"]
                if types and names:
                    variable = names[-1].child_by_field_name("name") or names[-1].named_children[0]
                    consumer = f"{class_name}.{_text(variable, source)}"
                    contract = _text(types[-1], source)
                    evidence = _evidence(path, root, field)
                    result.edges.append(FlowEdge(consumer, contract, "injects", evidence))
                    result.injections.append(Injection(consumer, contract, _first_qualifier(_text(field, source)), evidence))
            for method_node in (node for node in _walk(class_node) if node.type == "method_declaration"):
                name_node = method_node.child_by_field_name("name")
                body = method_node.child_by_field_name("body")
                if name_node is None or body is None:
                    continue
                name = _text(name_node, source)
                symbol = f"{class_name}.{name}"
                function = _Function(name, symbol, body, method_node)
                result.symbols.append(_symbol(function, path, root, implements, qualifiers=qualifiers, primary=primary))
                result.edges.extend(self._edges_for(function, path, root, source))
                modifiers = next((node for node in method_node.named_children if node.type == "modifiers"), None)
                modifier_text = _text(modifiers, source) if modifiers else ""
                match = re.search(r"@(GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping)\s*\(\s*\"([^\"]+)\"", modifier_text)
                if match:
                    result.entrypoints.append(EntryPoint("http", self.ROUTES[match.group(1)], match.group(2), symbol, _evidence(path, root, method_node)))
                    result.contracts[symbol] = _spring_http_contract(_text(method_node, source), modifier_text)
                listener = re.search(r"@RabbitListener\s*\([^)]*(?:queues\s*=\s*)?\"([^\"]+)\"", modifier_text)
                if listener:
                    result.entrypoints.append(EntryPoint("message", "CONSUME", listener.group(1), symbol, _evidence(path, root, method_node)))
        return result


class _JvmSpringAnalyzer:
    """Selects the JVM parser while keeping the public stack identifier stable."""

    def __init__(self) -> None:
        self._kotlin = _KotlinSpringAnalyzer(Language(tree_sitter_kotlin.language()))
        self._java = _JavaSpringAnalyzer(Language(tree_sitter_java.language()))

    def analyze(self, path: Path, root: Path) -> AnalysisResult:
        return self._java.analyze(path, root) if path.suffix == ".java" else self._kotlin.analyze(path, root)


class _NodeGraphqlAnalyzer(_FileAnalyzer):
    def analyze(self, path: Path, root: Path) -> AnalysisResult:
        if path.suffix in {".graphql", ".gql"}:
            return _GraphqlContractExtractor().analyze(path, root)
        source = path.read_bytes()
        tree = self.parse(source)
        result = AnalysisResult()
        imports = _node_named_imports(source.decode("utf-8", errors="ignore"))
        for node in _walk(tree):
            if node.type != "function_declaration":
                continue
            name_node = node.child_by_field_name("name")
            body = node.child_by_field_name("body")
            if name_node is None or body is None:
                continue
            name = _text(name_node, source)
            function = _Function(name, f"{path.stem}.{name}", body, node)
            result.symbols.append(_symbol(function, path, root, imports=imports))
            result.edges.extend(self._edges_for(function, path, root, source))
        for parent in _walk(tree):
            if parent.type != "pair" or _text(parent.child_by_field_name("key"), source) not in {"Query", "Mutation", "Subscription"}:
                continue
            operation = _text(parent.child_by_field_name("key"), source)
            value = parent.child_by_field_name("value")
            if value is None or value.type != "object":
                continue
            for resolver in (node for node in value.named_children if node.type == "pair"):
                name_node = resolver.child_by_field_name("key")
                handler = resolver.child_by_field_name("value")
                if name_node is None or handler is None:
                    continue
                name = _text(name_node, source)
                symbol = f"{operation}.{name}"
                result.entrypoints.append(EntryPoint("graphql", operation.upper(), name, symbol, _evidence(path, root, resolver)))
                function = _Function(name, symbol, handler, resolver)
                result.symbols.append(_symbol(function, path, root, imports=imports))
                result.edges.extend(self._edges_for(function, path, root, source))
        for node in _walk(tree):
            if node.type != "call_expression":
                continue
            callee = node.child_by_field_name("function")
            arguments = node.child_by_field_name("arguments")
            if callee is None or arguments is None or not _text(callee, source).endswith(".consume"):
                continue
            args = arguments.named_children
            channel = _string(args[0], source) if args else None
            handler = args[1] if len(args) > 1 else None
            if channel is None or handler is None or handler.type not in {"arrow_function", "function_expression"}:
                continue
            symbol = f"message.consume:{channel}"
            result.entrypoints.append(EntryPoint("message", "CONSUME", channel, symbol, _evidence(path, root, node)))
            function = _Function(channel, symbol, handler, node)
            result.symbols.append(_symbol(function, path, root, imports=imports))
            result.edges.extend(self._edges_for(function, path, root, source))
        return result


class _GraphqlContractExtractor:
    """Extracts GraphQL schemas into compact, deterministic entrypoint contracts."""

    _TYPE_BLOCK = re.compile(r"\btype\s+(Query|Mutation|Subscription)\s*\{(?P<body>.*?)\}", re.DOTALL)
    _INPUT_BLOCK = re.compile(r"\binput\s+(\w+)\s*\{(?P<body>.*?)\}", re.DOTALL)
    _FIELD = re.compile(r"\b(\w+)\s*(?:\(([^)]*)\))?\s*:\s*([\[\]\w!]+)")
    _INPUT_FIELD = re.compile(r"\b(\w+)\s*:\s*([\[\]\w!]+)")

    def analyze(self, path: Path, root: Path) -> AnalysisResult:
        source = path.read_text(encoding="utf-8", errors="ignore")
        input_fields = {name: self._fields(body) for name, body in self._INPUT_BLOCK.findall(source)}
        result = AnalysisResult()
        for operation, body in self._TYPE_BLOCK.findall(source):
            for name, arguments, return_type in self._FIELD.findall(body):
                result.contracts[f"{operation}.{name}"] = {
                    "arguments": self._arguments(arguments, input_fields),
                    "returns": self._type_shape(return_type),
                }
        return result

    def _arguments(self, text: str, input_fields: dict[str, list[dict]]) -> list[dict]:
        return [
            {"name": name, **self._type_shape(type_name), "fields": input_fields.get(self._base_type(type_name), [])}
            for name, type_name in self._INPUT_FIELD.findall(text)
        ]

    def _fields(self, text: str) -> list[dict]:
        return [{"name": name, **self._type_shape(type_name)} for name, type_name in self._INPUT_FIELD.findall(text)]

    @staticmethod
    def _base_type(type_name: str) -> str:
        return type_name.rstrip("!").strip("[]")

    def _type_shape(self, type_name: str) -> dict:
        return {"type": self._base_type(type_name), "required": type_name.endswith("!")}


class _PythonCliAnalyzer:
    """A small built-in AST analyzer used to dogfood CLI entrypoints.

    Python is intentionally limited to explicit ``main`` functions here; service
    HTTP discovery remains the existing framework detector until it receives its
    own flow analyzer.
    """

    def analyze(self, path: Path, root: Path) -> AnalysisResult:
        text = path.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            return AnalysisResult()
        result = AnalysisResult()
        for function in (node for node in tree.body if isinstance(node, ast.FunctionDef)):
            symbol = f"{path.stem}.{function.name}"
            evidence = Evidence(path.relative_to(root).as_posix(), function.lineno, function.end_lineno or function.lineno)
            result.symbols.append(Symbol(symbol, path.stem, function.name, evidence))
            if function.name == "main":
                result.entrypoints.append(EntryPoint("cli", "COMMAND", path.stem, symbol, evidence))
            for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
                target = _python_call_name(call.func)
                if target:
                    result.edges.append(
                        FlowEdge(symbol, target, _call_kind(target), Evidence(
                            path.relative_to(root).as_posix(), call.lineno, call.end_lineno or call.lineno,
                        ))
                    )
        return result


def _python_call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _python_call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return None


def _kotlin_supertypes(class_text: str) -> tuple[str, ...]:
    match = re.search(r"\bclass\s+\w+\s*:\s*([^\{(]+)", class_text)
    return tuple(item.strip().split("(", 1)[0] for item in match.group(1).split(",")) if match else ()


def _go_http_contract(declaration: str) -> dict:
    variables = dict(re.findall(r"\bvar\s+(\w+)\s+([\w\[\]*]+)", declaration))
    decoded = re.search(r"\.Decode\s*\(\s*&?(\w+)\s*\)", declaration)
    request_name = decoded.group(1) if decoded else None
    request_type = variables.get(request_name or "")
    return {
        "request": {"name": request_name, "type": request_type, "required": True} if request_type else None,
        "returns": None,
        "validations": [],
        "authorization": [],
    }


def _java_interfaces(class_text: str) -> tuple[str, ...]:
    match = re.search(r"\bimplements\s+([^\{]+)", class_text)
    return tuple(item.strip() for item in match.group(1).split(",")) if match else ()


def _spring_http_contract(declaration: str, annotations: str, kotlin: bool = False) -> dict:
    request = _spring_request(declaration, kotlin)
    return_type = _spring_return_type(declaration, kotlin)
    validations = re.findall(r"@(Valid|Validated|NotNull|NotBlank|NotEmpty|Positive|Negative|Size|Pattern)\b", declaration)
    authorization = re.findall(r"@(PreAuthorize|Secured|RolesAllowed)\b", annotations)
    return {
        "request": request,
        "returns": {"type": return_type, "required": True} if return_type else None,
        "validations": validations,
        "authorization": authorization,
    }


def _spring_request(declaration: str, kotlin: bool) -> dict | None:
    if kotlin:
        match = re.search(r"@RequestBody\s+(\w+)\s*:\s*([\w<>?]+)", declaration)
        return {"name": match.group(1), "type": match.group(2).rstrip("?"), "required": not match.group(2).endswith("?")} if match else None
    match = re.search(r"@RequestBody\s+([\w<>]+)\s+(\w+)", declaration)
    return {"name": match.group(2), "type": match.group(1), "required": True} if match else None


def _spring_return_type(declaration: str, kotlin: bool) -> str | None:
    if kotlin:
        match = re.search(r"\)\s*:\s*([\w<>?]+)", declaration)
    else:
        match = re.search(r"\b([A-Z][\w<>]*)\s+\w+\s*\(", declaration)
    return match.group(1).rstrip("?") if match else None


def _class_annotations(class_node: Node, source: bytes) -> str:
    modifiers = next((node for node in class_node.named_children if node.type == "modifiers"), None)
    return _text(modifiers, source) if modifiers else ""


def _qualifiers(source: str) -> tuple[str, ...]:
    return tuple(re.findall(r'@(?:Qualifier|Service|Component|Repository)\s*\(\s*"([^"]+)"', source))


def _first_qualifier(source: str) -> str | None:
    qualifiers = _qualifiers(source)
    return qualifiers[0] if qualifiers else None


def _go_package_name(source: str, path: Path) -> str:
    match = re.search(r"(?m)^\s*package\s+(\w+)", source)
    return match.group(1) if match else path.parent.name


def _go_imports(source: str) -> tuple[tuple[str, str], ...]:
    blocks = re.findall(r"(?ms)^\s*import\s*\((.*?)^\s*\)", source)
    single_imports = re.findall(r'(?m)^\s*import\s+(?:(\w+)\s+)?"([^"]+)"', source)
    declarations = [
        item
        for block in blocks
        for item in re.findall(r'(?m)^\s*(?:(\w+)\s+)?"([^"]+)"', block)
    ]
    imports = []
    for alias, module in [*single_imports, *declarations]:
        package = module.rstrip("/").rsplit("/", 1)[-1]
        local_name = alias or package
        if local_name not in {"_", "."}:
            imports.append((local_name, package))
    return tuple(imports)


def _node_named_imports(source: str) -> tuple[tuple[str, str], ...]:
    imports = []
    for names, module in re.findall(r"import\s*\{([^}]+)\}\s*from\s*[\"']([^\"']+)[\"']", source):
        module_name = Path(module).name
        for item in names.split(","):
            original, _as, local = item.strip().partition(" as ")
            imports.append(((local or original).strip(), f"{module_name}.{original.strip()}"))
    return tuple(imports)


class StaticAnalysisEngine:
    """Facade selecting an AST analyzer for the supported service stack."""

    def __init__(self, depth_provider: DepthProvider | None = None) -> None:
        self._depth_provider = depth_provider or NoopDepthProvider()
        self._analyzers = {
            "go": (_GoAnalyzer(Language(tree_sitter_go.language())), ("*.go",)),
            "jvm-spring": (_JvmSpringAnalyzer(), ("*.java", "*.kt")),
            "node-ts": (_NodeGraphqlAnalyzer(Language(tree_sitter_typescript.language_typescript())), ("*.ts", "*.tsx", "*.graphql", "*.gql")),
            "node-js": (_NodeGraphqlAnalyzer(Language(tree_sitter_javascript.language())), ("*.js", "*.jsx", "*.graphql", "*.gql")),
            "python": (_PythonCliAnalyzer(), ("*.py",)),
        }

    def analyze(self, root: Path, stack: str) -> AnalysisResult:
        configured = self._analyzers.get(stack)
        if configured is None:
            return AnalysisResult()
        analyzer, patterns = configured
        result = AnalysisResult()
        files = sorted({
            path
            for pattern in patterns
            for path in root.rglob(pattern)
            if not any(part in SKIP_DIRS for part in path.relative_to(root).parts)
        })
        for path in files:
            result.extend(analyzer.analyze(path, root))
        _enrich_contract_fields(result.contracts, files)
        result = BoundedFlowResolver().resolve(result)
        result.edges.extend(self._depth_provider.enrich(root, result))
        return result


def _enrich_contract_fields(contracts: dict[str, dict], files: list[Path]) -> None:
    shapes = _dto_shapes(files)
    for contract in contracts.values():
        for key in ("request", "returns"):
            value = contract.get(key)
            if value and (fields := shapes.get(value["type"])):
                value["fields"] = fields


def _dto_shapes(files: list[Path]) -> dict[str, list[dict]]:
    shapes: dict[str, list[dict]] = {}
    for path in files:
        source = path.read_text(encoding="utf-8", errors="ignore")
        shapes.update(_java_dto_shapes(source))
        shapes.update(_go_dto_shapes(source))
    return shapes


def _java_dto_shapes(source: str) -> dict[str, list[dict]]:
    shapes = {}
    for name, body in re.findall(r"\bclass\s+(\w+)[^{]*\{(.*?)\}", source, re.DOTALL):
        fields = []
        for field_annotations, type_name, field_name in re.findall(
            r"((?:\s*@\w+(?:\([^)]*\))?\s*)*)([A-Z]\w*(?:<[^>]+>)?)\s+(\w+)\s*;", body,
        ):
            validations = re.findall(r"@(NotNull|NotBlank|NotEmpty|Positive|Negative|Size|Pattern)\b", field_annotations)
            fields.append({"name": field_name, "type": type_name, "required": bool(validations), "validations": validations})
        if fields:
            shapes[name] = fields
    return shapes


def _go_dto_shapes(source: str) -> dict[str, list[dict]]:
    shapes = {}
    for name, body in re.findall(r"\btype\s+(\w+)\s+struct\s*\{(.*?)\}", source, re.DOTALL):
        fields = []
        for field_name, type_name, tags in re.findall(r"(?m)^\s*(\w+)\s+([\w*\[\]]+)(?:\s+`([^`]*)`)?", body):
            json_name = re.search(r'json:"([^,"]+)', tags)
            validations = ["required"] if re.search(r'validate:"[^"]*\brequired\b', tags) else []
            fields.append({"name": json_name.group(1) if json_name else field_name, "type": type_name.lstrip("*"), "required": bool(validations), "validations": validations})
        if fields:
            shapes[name] = fields
    return shapes
