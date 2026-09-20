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
import tree_sitter_javascript
import tree_sitter_kotlin
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from orbitkb.analysis.depth import DepthProvider, NoopDepthProvider
from orbitkb.analysis.models import AnalysisResult, EntryPoint, Evidence, FlowEdge
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
            if node.type != "call_expression":
                continue
            # Grammar field names differ (Kotlin exposes the callee as the first
            # named child while Go/TypeScript call it `function`). Normalize that
            # syntax detail at the parser boundary.
            callee = node.child_by_field_name("function") or (node.named_children[0] if node.named_children else None)
            if callee is None:
                continue
            target = _text(callee, source)
            edges.append(FlowEdge(function.symbol, target, _call_kind(target), _evidence(path, root, node)))
        return edges


class _GoAnalyzer(_FileAnalyzer):
    ROUTE_METHODS: ClassVar[frozenset[str]] = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})

    def analyze(self, path: Path, root: Path) -> AnalysisResult:
        source = path.read_bytes()
        tree = self.parse(source)
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
            functions.append(_Function(name, f"{receiver}.{name}".strip("."), body, node))

        result = AnalysisResult(edges=[edge for fn in functions for edge in self._edges_for(fn, path, root, source)])
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
            for parameter in (node for node in _walk(class_node) if node.type == "class_parameter"):
                types = [node for node in _walk(parameter) if node.type == "user_type"]
                if types:
                    result.edges.append(FlowEdge(class_name, _text(types[-1], source), "injects", _evidence(path, root, parameter)))
            for function_node in (node for node in _walk(class_node) if node.type == "function_declaration"):
                name_node = function_node.child_by_field_name("name")
                if name_node is None:
                    continue
                symbol = f"{class_name}.{_text(name_node, source)}"
                body = function_node.child_by_field_name("body") or function_node
                function = _Function(_text(name_node, source), symbol, body, function_node)
                result.edges.extend(self._edges_for(function, path, root, source))
                modifiers = next((node for node in function_node.named_children if node.type == "modifiers"), None)
                modifier_text = _text(modifiers, source) if modifiers else ""
                match = re.search(r"@(GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping)\s*\(\s*\"([^\"]+)\"", modifier_text)
                if match:
                    result.entrypoints.append(EntryPoint("http", self.ROUTES[match.group(1)], match.group(2), symbol, _evidence(path, root, function_node)))
                listener = re.search(r"@RabbitListener\s*\([^)]*\[\s*\"([^\"]+)\"", modifier_text)
                if listener:
                    result.entrypoints.append(EntryPoint("message", "CONSUME", listener.group(1), symbol, _evidence(path, root, function_node)))
        return result


class _NodeGraphqlAnalyzer(_FileAnalyzer):
    def analyze(self, path: Path, root: Path) -> AnalysisResult:
        source = path.read_bytes()
        tree = self.parse(source)
        result = AnalysisResult()
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
            result.edges.extend(self._edges_for(_Function(channel, symbol, handler, node), path, root, source))
        return result


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
        for function in (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"):
            symbol = f"{path.stem}.main"
            evidence = Evidence(path.relative_to(root).as_posix(), function.lineno, function.end_lineno or function.lineno)
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


class StaticAnalysisEngine:
    """Facade selecting an AST analyzer for the supported service stack."""

    def __init__(self, depth_provider: DepthProvider | None = None) -> None:
        self._depth_provider = depth_provider or NoopDepthProvider()
        self._analyzers = {
            "go": (_GoAnalyzer(Language(tree_sitter_go.language())), ("*.go",)),
            "jvm-spring": (_KotlinSpringAnalyzer(Language(tree_sitter_kotlin.language())), ("*.kt",)),
            "node-ts": (_NodeGraphqlAnalyzer(Language(tree_sitter_typescript.language_typescript())), ("*.ts", "*.tsx")),
            "node-js": (_NodeGraphqlAnalyzer(Language(tree_sitter_javascript.language())), ("*.js", "*.jsx")),
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
        result.edges.extend(self._depth_provider.enrich(root, result))
        return result
