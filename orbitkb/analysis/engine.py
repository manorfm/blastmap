"""Tree-sitter based, transport-aware static analysis.

This module deliberately produces a small flow model, not a generic code graph.
It parses source locally (zero LLM tokens) and records only entrypoints plus the
calls, persistence operations and messages reachable from their declared handler.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, replace
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
    FlowBoundary,
    FlowEdge,
    Injection,
    MessageContract,
    PersistenceFact,
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


_MONGOOSE_READ_METHODS = frozenset({
    "aggregate", "countdocuments", "distinct", "estimateddocumentcount", "exists",
    "find", "findbyid", "findone",
})
_MONGOOSE_WRITE_METHODS = frozenset({
    "bulkwrite", "create", "deletemany", "deleteone", "findbyidanddelete",
    "findbyidandupdate", "findoneanddelete", "findoneandupdate", "insertmany",
    "replaceone", "updatemany", "updateone",
})
_PRISMA_READ_METHODS = frozenset({
    "aggregate", "count", "findfirst", "findfirstorthrow", "findmany", "findunique",
    "finduniqueorthrow", "groupby",
})
_PRISMA_WRITE_METHODS = frozenset({
    "create", "createmany", "createmanyandreturn", "delete", "deletemany",
    "deletemanyandreturn", "update", "updatemany", "updatemanyandreturn", "upsert",
})
_GORM_READ_METHODS = frozenset({
    "count", "find", "findinbatches", "first", "last", "pluck", "row", "rows", "scan", "take",
})
_GORM_WRITE_METHODS = frozenset({
    "create", "delete", "exec", "save", "update", "updatecolumn", "updatecolumns", "updates",
})
_DATABASE_SQL_READ_METHODS = frozenset({"Query", "QueryContext", "QueryRow", "QueryRowContext"})
_DATABASE_SQL_WRITE_METHODS = frozenset({"Exec", "ExecContext"})
_SPRING_REPOSITORY_READ_METHODS = frozenset({
    "count", "existsById", "findAll", "findAllById", "findById", "getById", "getOne", "getReferenceById",
})
_SPRING_REPOSITORY_WRITE_METHODS = frozenset({
    "delete", "deleteAll", "deleteAllById", "deleteAllByIdInBatch", "deleteAllInBatch",
    "deleteById", "deleteInBatch", "flush", "save", "saveAll", "saveAndFlush",
})
_SPRING_DATA_REPOSITORY_BASE_TYPES = frozenset({
    "CrudRepository", "JpaRepository", "ListCrudRepository", "ListPagingAndSortingRepository",
    "MongoRepository", "PagingAndSortingRepository", "ReactiveCrudRepository", "ReactiveMongoRepository",
})
_SPRING_JDBC_TEMPLATE_TYPES = frozenset({"JdbcTemplate", "NamedParameterJdbcTemplate"})
_SPRING_MONGO_TEMPLATE_TYPES = frozenset({"MongoTemplate", "ReactiveMongoTemplate"})
_SPRING_MONGO_READ_METHODS = frozenset({"aggregate", "count", "distinct", "exists", "find", "findById", "findOne"})
_SPRING_MONGO_WRITE_METHODS = frozenset({
    "findAndModify", "findAndReplace", "insert", "insertAll", "remove", "save", "updateFirst", "updateMulti", "upsert",
})
_ENTITY_MANAGER_READ_METHODS = frozenset({"find", "getReference"})
_ENTITY_MANAGER_WRITE_METHODS = frozenset({"flush", "merge", "persist", "remove"})


def _mongoose_model_variables(source: str) -> frozenset[str]:
    """Return names locally declared through the unambiguous Mongoose factory."""
    return frozenset(re.findall(
        r"\b(?:const|let|var)\s+(\w+)\s*=\s*mongoose\.model\s*(?:<[^>]+>)?\s*\(", source,
    ))


def _mongoose_call_kind(target: str, model_variables: frozenset[str]) -> str | None:
    """Classify only exact operations on a locally declared Mongoose model."""
    receiver, separator, method = target.rpartition(".")
    if not separator or receiver not in model_variables:
        return None
    if method.lower() in _MONGOOSE_READ_METHODS:
        return "reads"
    if method.lower() in _MONGOOSE_WRITE_METHODS:
        return "writes"
    return None


def _prisma_client_variables(source: str) -> frozenset[str]:
    """Return names locally constructed through the explicit Prisma client type."""
    return frozenset(re.findall(
        r"\b(?:const|let|var)\s+(\w+)\s*=\s*new\s+PrismaClient\s*(?:<[^>]+>)?\s*\(", source,
    ))


def _prisma_call_kind(target: str, client_variables: frozenset[str]) -> str | None:
    """Classify exact model delegates on a locally constructed Prisma client."""
    client, separator, delegate_and_method = target.partition(".")
    delegate, separator, method = delegate_and_method.partition(".")
    if not separator or client not in client_variables or not delegate.isidentifier():
        return None
    if method.lower() in _PRISMA_READ_METHODS:
        return "reads"
    if method.lower() in _PRISMA_WRITE_METHODS:
        return "writes"
    return None


def _gorm_db_parameters(declaration: str) -> frozenset[str]:
    """Return direct parameters whose GORM database type is locally explicit."""
    return frozenset(re.findall(r"\b(\w+)\s+\*gorm\.DB\b", declaration))


def _gorm_call_kind(target: str, db_parameters: frozenset[str]) -> str | None:
    """Classify direct or simple fluent operations on an explicit GORM parameter."""
    receiver, separator, method = target.rpartition(".")
    root = receiver if receiver in db_parameters else _gorm_fluent_root(receiver)
    if not separator or root not in db_parameters:
        return None
    if method.lower() in _GORM_READ_METHODS:
        return "reads"
    if method.lower() in _GORM_WRITE_METHODS:
        return "writes"
    return None


def _gorm_fluent_root(receiver: str) -> str | None:
    """Return the root of a simple Go fluent chain without parsing arbitrary calls."""
    match = re.fullmatch(r"(\w+)(?:\.\w+\([^()]*\))*", receiver)
    return match.group(1) if match else None


def _database_sql_parameters(declaration: str) -> frozenset[str]:
    """Return direct parameters whose standard-library database type is explicit."""
    return frozenset(re.findall(r"\b(\w+)\s+\*sql\.(?:DB|Tx)\b", declaration))


def _database_sql_call_kind(target: str, parameters: frozenset[str]) -> str | None:
    """Classify exact `database/sql` query and execution calls on local parameters."""
    receiver, separator, method = target.rpartition(".")
    if not separator or receiver not in parameters:
        return None
    if method in _DATABASE_SQL_READ_METHODS:
        return "reads"
    if method in _DATABASE_SQL_WRITE_METHODS:
        return "writes"
    return None


def _spring_repository_receivers(injections: list[Injection], class_name: str) -> frozenset[str]:
    """Return locally injected members whose declared type is a Spring repository."""
    prefix = f"{class_name}."
    return frozenset(
        injection.consumer.removeprefix(prefix)
        for injection in injections
        if injection.consumer.startswith(prefix) and _is_spring_repository_type(injection.contract)
    )


def _is_spring_repository_type(contract: str) -> bool:
    type_name = contract.split("<", 1)[0].rsplit(".", 1)[-1]
    return type_name.endswith("Repository") or type_name in _SPRING_DATA_REPOSITORY_BASE_TYPES


def _spring_repository_call_kind(target: str, receivers: frozenset[str]) -> str | None:
    """Classify exact standard operations on a locally injected repository member."""
    receiver, separator, method = target.rpartition(".")
    if not separator or receiver not in receivers:
        return None
    if method in _SPRING_REPOSITORY_READ_METHODS:
        return "reads"
    if method in _SPRING_REPOSITORY_WRITE_METHODS:
        return "writes"
    return None


def _spring_jdbc_template_receivers(injections: list[Injection], class_name: str) -> frozenset[str]:
    """Return locally injected members whose declared type is a Spring JDBC template."""
    prefix = f"{class_name}."
    return frozenset(
        injection.consumer.removeprefix(prefix)
        for injection in injections
        if injection.consumer.startswith(prefix)
        and injection.contract.split("<", 1)[0].rsplit(".", 1)[-1] in _SPRING_JDBC_TEMPLATE_TYPES
    )


def _spring_jdbc_template_call_kind(target: str, receivers: frozenset[str]) -> str | None:
    """Classify exact query/update operations on a local Spring JDBC template."""
    receiver, separator, method = target.rpartition(".")
    if not separator or receiver not in receivers:
        return None
    if method.startswith("query"):
        return "reads"
    if method in {"update", "batchUpdate"}:
        return "writes"
    return None


def _spring_mongo_template_receivers(injections: list[Injection], class_name: str) -> frozenset[str]:
    """Return locally injected members whose declared type is a Spring Mongo template."""
    prefix = f"{class_name}."
    return frozenset(
        injection.consumer.removeprefix(prefix)
        for injection in injections
        if injection.consumer.startswith(prefix)
        and injection.contract.split("<", 1)[0].rsplit(".", 1)[-1] in _SPRING_MONGO_TEMPLATE_TYPES
    )


def _spring_mongo_template_call_kind(target: str, receivers: frozenset[str]) -> str | None:
    """Classify exact Mongo operations on a locally injected Spring template."""
    receiver, separator, method = target.rpartition(".")
    if not separator or receiver not in receivers:
        return None
    if method in _SPRING_MONGO_READ_METHODS:
        return "reads"
    if method in _SPRING_MONGO_WRITE_METHODS:
        return "writes"
    return None


def _entity_manager_receivers(injections: list[Injection], class_name: str) -> frozenset[str]:
    """Return locally injected members whose declared type is JPA EntityManager."""
    prefix = f"{class_name}."
    return frozenset(
        injection.consumer.removeprefix(prefix)
        for injection in injections
        if injection.consumer.startswith(prefix)
        and injection.contract.split("<", 1)[0].rsplit(".", 1)[-1] == "EntityManager"
    )


def _entity_manager_call_kind(target: str, receivers: frozenset[str]) -> str | None:
    """Classify exact JPA EntityManager operations on a local dependency."""
    receiver, separator, method = target.rpartition(".")
    if not separator or receiver not in receivers:
        return None
    if method in _ENTITY_MANAGER_READ_METHODS:
        return "reads"
    if method in _ENTITY_MANAGER_WRITE_METHODS:
        return "writes"
    return None


@dataclass(frozen=True)
class _SpringPersistenceReceivers:
    repositories: frozenset[str]
    jdbc_templates: frozenset[str]
    mongo_templates: frozenset[str]
    entity_managers: frozenset[str]


def _spring_persistence_receivers(
    injections: list[Injection], class_name: str,
) -> _SpringPersistenceReceivers:
    """Collect locally declared Spring persistence dependencies for one class."""
    return _SpringPersistenceReceivers(
        _spring_repository_receivers(injections, class_name),
        _spring_jdbc_template_receivers(injections, class_name),
        _spring_mongo_template_receivers(injections, class_name),
        _entity_manager_receivers(injections, class_name),
    )


def _spring_edges_for(
    function: _Function,
    path: Path,
    root: Path,
    source: bytes,
    receivers: _SpringPersistenceReceivers,
) -> list[FlowEdge]:
    edges = _FileAnalyzer._edges_for(function, path, root, source)
    classified = []
    for edge in edges:
        kind = (
            _spring_repository_call_kind(edge.target, receivers.repositories)
            or _spring_jdbc_template_call_kind(edge.target, receivers.jdbc_templates)
            or _spring_mongo_template_call_kind(edge.target, receivers.mongo_templates)
            or _entity_manager_call_kind(edge.target, receivers.entity_managers)
        )
        # Generic name matching is disabled for JVM persistence: `repository.save`
        # is an operation only with a local repository dependency.
        if kind is None and edge.kind in {"reads", "writes"}:
            kind = "invokes"
        classified.append(FlowEdge(
            edge.source, edge.target, kind or edge.kind, edge.evidence, edge.confidence, edge.origin,
        ))
    return classified


def _spring_data_repository_types(files: list[Path]) -> frozenset[str]:
    """Find local interfaces whose declaration proves a Spring Data contract."""
    types = set()
    for path in files:
        source = path.read_text(encoding="utf-8", errors="ignore")
        for name, parents in re.findall(r"\binterface\s+(\w+)\s*(?:extends|:)\s*([^\{]+)\{", source):
            if any(re.search(rf"\b{base}\b", parents) for base in _SPRING_DATA_REPOSITORY_BASE_TYPES):
                types.add(name)
    return frozenset(types)


def _classify_spring_data_derived_operations(
    edges: list[FlowEdge], injections: list[Injection], repository_types: frozenset[str],
) -> list[FlowEdge]:
    """Classify derived methods only from local interface and injection evidence."""
    injected_types = {
        injection.consumer: injection.contract.split("<", 1)[0].rsplit(".", 1)[-1]
        for injection in injections
    }
    classified = []
    for edge in edges:
        owner, separator, _member = edge.source.rpartition(".")
        receiver, target_separator, method = edge.target.rpartition(".")
        repository_type = injected_types.get(f"{owner}.{receiver}") if separator and target_separator else None
        kind = _spring_derived_operation_kind(method) if repository_type in repository_types else None
        classified.append(replace(edge, kind=kind) if kind else edge)
    return classified


def _spring_derived_operation_kind(method: str) -> str | None:
    if method.startswith(("countBy", "existsBy", "findBy", "getBy", "queryBy", "readBy", "streamBy")):
        return "reads"
    if method.startswith(("deleteBy", "removeBy")):
        return "writes"
    return None


def _spring_data_query_methods(files: list[Path]) -> dict[tuple[str, str], str]:
    """Map local Spring Data `@Query` declarations to their proven operation kind."""
    methods = {}
    for path in files:
        source = path.read_text(encoding="utf-8", errors="ignore")
        for repository, parents, body in re.findall(
            r"\binterface\s+(\w+)\s*(?:extends|:)\s*([^\{]+)\{(.*?)\}", source, re.DOTALL,
        ):
            if not any(re.search(rf"\b{base}\b", parents) for base in _SPRING_DATA_REPOSITORY_BASE_TYPES):
                continue
            for match in re.finditer(
                r"@Query\s*\((?:[^()]|\([^()]*\))*\)\s*"
                r"(?P<annotations>(?:@\w+(?:\s*\([^)]*\))?\s*)*)"
                r"(?:public\s+)?(?:[\w.<>,?\[\]]+\s+)?(?P<method>\w+)\s*\(",
                body,
                re.DOTALL,
            ):
                methods[(repository, match.group("method"))] = (
                    "writes" if "@Modifying" in match.group("annotations") else "reads"
                )
    return methods


def _classify_spring_data_query_operations(
    edges: list[FlowEdge], injections: list[Injection], query_methods: dict[tuple[str, str], str],
) -> list[FlowEdge]:
    """Apply only exact local `@Query` method declarations to observed calls."""
    injected_types = {
        injection.consumer: injection.contract.split("<", 1)[0].rsplit(".", 1)[-1]
        for injection in injections
    }
    classified = []
    for edge in edges:
        owner, separator, _member = edge.source.rpartition(".")
        receiver, target_separator, method = edge.target.rpartition(".")
        repository_type = injected_types.get(f"{owner}.{receiver}") if separator and target_separator else None
        kind = query_methods.get((repository_type, method))
        classified.append(replace(edge, kind=kind) if kind else edge)
    return classified


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

    @staticmethod
    def _boundaries_for(function: _Function, path: Path, root: Path, source: bytes) -> list[FlowBoundary]:
        text = _text(function.declaration, source)
        patterns = {
            "branch": r"\bif\b|\bwhen\b",
            "async": r"\bawait\b|\basync\b|\bgo\s+",
            "retry": r"\bretry\b|\bbackoff\b",
            "error": r"\bthrow\b|\bcatch\b|\bexcept\b|\breturn\s+err\b",
            "transaction": r"@Transactional|\btransaction\b",
        }
        evidence = _evidence(path, root, function.declaration)
        return [FlowBoundary(function.symbol, kind, evidence) for kind, pattern in patterns.items() if re.search(pattern, text)]


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
            edges=[edge for fn in functions for edge in self._edges_for_go(fn, path, root, source)],
            symbols=[_symbol(fn, path, root, imports=imports) for fn in functions],
            message_contracts=[
                contract
                for function in functions
                for contract in _go_amqp_publish_contracts(function, path, root, source)
            ],
        )
        by_last_name = {fn.name: fn for fn in functions}
        groups = _go_route_groups(tree, source)
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
            if callee_text.endswith(".Consume") and args and args[-1].type == "func_literal":
                channel = _string(args[0], source)
                body = args[-1].child_by_field_name("body")
                if channel and body:
                    symbol = f"message.consume:{channel}"
                    handler = _Function(channel, symbol, body, args[-1])
                    result.entrypoints.append(EntryPoint("message", "CONSUME", channel, symbol, _evidence(path, root, node)))
                    result.symbols.append(_symbol(handler, path, root, imports=imports))
                    result.edges.extend(self._edges_for_go(handler, path, root, source))
                    result.boundaries.extend(self._boundaries_for(handler, path, root, source))
                    result.contracts[symbol] = _message_contract(channel, _text(args[-1], source), "go")
            if method not in self.ROUTE_METHODS or len(args) < 2:
                continue
            route = _string(args[0], source)
            receiver = callee_text.rsplit(".", 1)[0] if "." in callee_text else ""
            route = _join_route(groups.get(receiver), route)
            handler = _text(args[-1], source).rsplit(".", 1)[-1]
            function = by_last_name.get(handler)
            if route and function:
                result.entrypoints.append(EntryPoint("http", method, route, function.symbol, _evidence(path, root, node)))
                result.contracts[function.symbol] = _go_http_contract(_text(function.declaration, source))
        return result

    @staticmethod
    def _edges_for_go(function: _Function, path: Path, root: Path, source: bytes) -> list[FlowEdge]:
        db_parameters = _gorm_db_parameters(_text(function.declaration, source))
        sql_parameters = _database_sql_parameters(_text(function.declaration, source))
        return [
            FlowEdge(
                edge.source,
                edge.target,
                _gorm_call_kind(edge.target, db_parameters)
                or _database_sql_call_kind(edge.target, sql_parameters)
                or edge.kind,
                edge.evidence,
                edge.confidence,
                edge.origin,
            )
            for edge in _FileAnalyzer._edges_for(function, path, root, source)
        ]


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
            route_prefix = _spring_route_prefix(annotations)
            qualifiers = _qualifiers(annotations)
            primary = "@Primary" in annotations
            publishers = _spring_amqp_publishers(_text(class_node, source))
            for parameter in (node for node in _walk(class_node) if node.type == "class_parameter"):
                types = [node for node in _walk(parameter) if node.type == "user_type"]
                if types:
                    name_match = re.search(r"(?:val|var)\s+(\w+)", _text(parameter, source))
                    injection_symbol = f"{class_name}.{name_match.group(1)}" if name_match else class_name
                    contract = _text(types[-1], source)
                    evidence = _evidence(path, root, parameter)
                    result.edges.append(FlowEdge(injection_symbol, contract, "injects", evidence))
                    result.injections.append(Injection(injection_symbol, contract, _first_qualifier(_text(parameter, source)), evidence))
            persistence_receivers = _spring_persistence_receivers(result.injections, class_name)
            for function_node in (node for node in _walk(class_node) if node.type == "function_declaration"):
                name_node = function_node.child_by_field_name("name")
                if name_node is None:
                    continue
                symbol = f"{class_name}.{_text(name_node, source)}"
                body = function_node.child_by_field_name("body") or function_node
                function = _Function(_text(name_node, source), symbol, body, function_node)
                result.symbols.append(_symbol(function, path, root, implements, qualifiers=qualifiers, primary=primary))
                result.edges.extend(_spring_edges_for(
                    function, path, root, source, persistence_receivers,
                ))
                result.boundaries.extend(self._boundaries_for(function, path, root, source))
                result.message_contracts.extend(
                    _spring_publish_contracts(_text(function_node, source), publishers, path, root, function_node, kotlin=True)
                )
                modifiers = next((node for node in function_node.named_children if node.type == "modifiers"), None)
                modifier_text = _text(modifiers, source) if modifiers else ""
                match = re.search(r"@(GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping)\s*\(\s*\"([^\"]+)\"", modifier_text)
                if match:
                    result.entrypoints.append(EntryPoint("http", self.ROUTES[match.group(1)], _join_route(route_prefix, match.group(2)), symbol, _evidence(path, root, function_node)))
                    result.contracts[symbol] = _spring_http_contract(_text(function_node, source), modifier_text, kotlin=True)
                listener = re.search(r"@RabbitListener\s*\([^)]*\[\s*\"([^\"]+)\"", modifier_text)
                if listener:
                    result.entrypoints.append(EntryPoint("message", "CONSUME", listener.group(1), symbol, _evidence(path, root, function_node)))
                    result.contracts[symbol] = _message_contract(listener.group(1), _text(function_node, source), "kotlin")
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
            route_prefix = _spring_route_prefix(annotations)
            qualifiers = _qualifiers(annotations)
            primary = "@Primary" in annotations
            publishers = _spring_amqp_publishers(_text(class_node, source))
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
            persistence_receivers = _spring_persistence_receivers(result.injections, class_name)
            for method_node in (node for node in _walk(class_node) if node.type == "method_declaration"):
                name_node = method_node.child_by_field_name("name")
                body = method_node.child_by_field_name("body")
                if name_node is None or body is None:
                    continue
                name = _text(name_node, source)
                symbol = f"{class_name}.{name}"
                function = _Function(name, symbol, body, method_node)
                result.symbols.append(_symbol(function, path, root, implements, qualifiers=qualifiers, primary=primary))
                result.edges.extend(_spring_edges_for(
                    function, path, root, source, persistence_receivers,
                ))
                result.boundaries.extend(self._boundaries_for(function, path, root, source))
                result.message_contracts.extend(
                    _spring_publish_contracts(_text(method_node, source), publishers, path, root, method_node)
                )
                modifiers = next((node for node in method_node.named_children if node.type == "modifiers"), None)
                modifier_text = _text(modifiers, source) if modifiers else ""
                match = re.search(r"@(GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping)\s*\(\s*\"([^\"]+)\"", modifier_text)
                if match:
                    result.entrypoints.append(EntryPoint("http", self.ROUTES[match.group(1)], _join_route(route_prefix, match.group(2)), symbol, _evidence(path, root, method_node)))
                    result.contracts[symbol] = _spring_http_contract(_text(method_node, source), modifier_text)
                listener = re.search(r"@RabbitListener\s*\([^)]*(?:queues\s*=\s*)?\"([^\"]+)\"", modifier_text)
                if listener:
                    result.entrypoints.append(EntryPoint("message", "CONSUME", listener.group(1), symbol, _evidence(path, root, method_node)))
                    result.contracts[symbol] = _message_contract(listener.group(1), _text(method_node, source), "java")
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
        if path.suffix == ".prisma":
            return AnalysisResult()
        source = path.read_bytes()
        source_text = source.decode("utf-8", errors="ignore")
        tree = self.parse(source)
        result = AnalysisResult()
        imports = _node_named_imports(source_text)
        mongoose_models = _mongoose_model_variables(source_text)
        prisma_clients = _prisma_client_variables(source_text)
        result.message_contracts.extend(_node_publish_contracts(tree, source, path, root))
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
            result.edges.extend(self._edges_for_node(function, path, root, source, mongoose_models, prisma_clients))
            result.boundaries.extend(self._boundaries_for(function, path, root, source))
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
                result.edges.extend(self._edges_for_node(function, path, root, source, mongoose_models, prisma_clients))
                result.boundaries.extend(self._boundaries_for(function, path, root, source))
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
            result.edges.extend(self._edges_for_node(function, path, root, source, mongoose_models, prisma_clients))
            result.boundaries.extend(self._boundaries_for(function, path, root, source))
            result.contracts[symbol] = _message_contract(channel, _text(handler, source), "node")
        return result

    @staticmethod
    def _edges_for_node(
        function: _Function, path: Path, root: Path, source: bytes, mongoose_models: frozenset[str],
        prisma_clients: frozenset[str],
    ) -> list[FlowEdge]:
        return [
            FlowEdge(
                edge.source,
                edge.target,
                _mongoose_call_kind(edge.target, mongoose_models)
                or _prisma_call_kind(edge.target, prisma_clients)
                or edge.kind,
                edge.evidence,
                edge.confidence,
                edge.origin,
            )
            for edge in _FileAnalyzer._edges_for(function, path, root, source)
        ]


class _GraphqlContractExtractor:
    """Extracts GraphQL schemas into compact, deterministic entrypoint contracts."""

    _TYPE_BLOCK = re.compile(r"\b(?:extend\s+)?type\s+(Query|Mutation|Subscription)\s*\{(?P<body>.*?)\}", re.DOTALL)
    _INPUT_BLOCK = re.compile(r"\binput\s+(\w+)\s*\{(?P<body>.*?)\}", re.DOTALL)
    _FIELD = re.compile(r"\b(\w+)\s*(?:\(([^)]*)\))?\s*:\s*([\[\]\w!]+)")
    _INPUT_FIELD = re.compile(r"\b(\w+)\s*:\s*([\[\]\w!]+)")
    _INTERFACE = re.compile(r"\binterface\s+(\w+)\s*\{", re.DOTALL)
    _IMPLEMENTS = re.compile(r"\btype\s+(\w+)\s+implements\s+([\w\s&]+)\s*\{", re.DOTALL)
    _UNION = re.compile(r"\bunion\s+(\w+)\s*=\s*([^\n]+)")

    def analyze(self, path: Path, root: Path) -> AnalysisResult:
        source = path.read_text(encoding="utf-8", errors="ignore")
        return AnalysisResult(contracts=self.contracts(source))

    def contracts(self, source: str) -> dict[str, dict]:
        input_fields = {name: self._fields(body) for name, body in self._INPUT_BLOCK.findall(source)}
        return_options = self._return_options(source)
        contracts = {}
        for operation, body in self._TYPE_BLOCK.findall(source):
            for name, arguments, return_type in self._FIELD.findall(body):
                contracts[f"{operation}.{name}"] = {
                    "arguments": self._arguments(arguments, input_fields),
                    "returns": {**self._type_shape(return_type), **return_options.get(self._base_type(return_type), {})},
                }
        return contracts

    def _arguments(self, text: str, input_fields: dict[str, list[dict]]) -> list[dict]:
        return [
            {"name": name, **self._type_shape(type_name), "fields": input_fields.get(self._base_type(type_name), [])}
            for name, type_name in self._INPUT_FIELD.findall(text)
        ]

    def _fields(self, text: str) -> list[dict]:
        return [{"name": name, **self._type_shape(type_name)} for name, type_name in self._INPUT_FIELD.findall(text)]

    def _return_options(self, source: str) -> dict[str, dict]:
        options: dict[str, set[str]] = {name: set() for name in self._INTERFACE.findall(source)}
        for type_name, interfaces in self._IMPLEMENTS.findall(source):
            for interface in interfaces.split("&"):
                if interface.strip() in options:
                    options[interface.strip()].add(type_name)
        for union, members in self._UNION.findall(source):
            options[union] = {member.strip() for member in members.split("|") if member.strip()}
        return {name: {"possible_types": sorted(members)} for name, members in options.items() if members}

    @staticmethod
    def _base_type(type_name: str) -> str:
        return type_name.replace("[", "").replace("]", "").rstrip("!")

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
    contract = {
        "request": {"name": request_name, "type": request_type, "required": True} if request_type else None,
        "returns": None,
        "validations": [],
        "authorization": [],
        "parameters": _go_bound_parameters(declaration),
    }
    if statuses := _go_response_statuses(declaration):
        contract["response_statuses"] = statuses
    return contract


def _message_contract(channel: str, declaration: str, language: str) -> dict:
    patterns = {
        "java": r"\(\s*([\w<>]+)\s+(\w+)",
        "kotlin": r"\(\s*(\w+)\s*:\s*([\w?]+)",
        "node": r"\(?\s*(\w+)\s*:\s*([\w?]+)",
        "go": r"func\s*\(\s*(\w+)\s+([\w*]+)",
    }
    match = re.search(patterns[language], declaration)
    if language == "java" and match:
        type_name, name = match.groups()
    elif match:
        name, type_name = match.groups()
    else:
        name = type_name = None
    return {
        "transport": "rabbitmq",
        "direction": "consumes",
        "queue": channel,
        "payload": {"name": name, "type": type_name.rstrip("?").lstrip("*") if type_name else None, "required": True} if type_name else None,
    }


def _node_publish_contracts(tree: Node, source: bytes, path: Path, root: Path) -> list[MessageContract]:
    contracts = []
    for node in _walk(tree):
        if node.type != "call_expression":
            continue
        callee = node.child_by_field_name("function")
        arguments = node.child_by_field_name("arguments")
        if callee is None or arguments is None or not _text(callee, source).endswith(".publish"):
            continue
        args = arguments.named_children
        channel = _string(args[0], source) if args else None
        routing_key = _string(args[1], source) if len(args) > 1 else None
        if channel:
            payload_type = _node_payload_type(node, args[2], source) if len(args) > 2 else None
            version = _message_header_version(_text(args[3], source)) if len(args) > 3 else None
            contracts.append(MessageContract("publishes", channel, routing_key, payload_type, _evidence(path, root, node), version))
    return contracts


def _node_payload_type(call: Node, payload: Node, source: bytes) -> str | None:
    payload_name = _text(payload, source).strip()
    if not re.fullmatch(r"\w+", payload_name):
        return None
    enclosing = call.parent
    while enclosing is not None:
        if enclosing.type in {"arrow_function", "function_declaration", "function_expression", "method_definition"}:
            parameters = enclosing.child_by_field_name("parameters")
            if parameters is not None:
                types = {
                    name: type_name.rstrip("?")
                    for name, type_name in re.findall(r"\b(\w+)\s*\??\s*:\s*([\w.$<>\[\]?]+)", _text(parameters, source))
                }
                if payload_type := types.get(payload_name):
                    return payload_type
        enclosing = enclosing.parent
    return None


def _go_amqp_publish_contracts(function: _Function, path: Path, root: Path, source: bytes) -> list[MessageContract]:
    declaration = _text(function.declaration, source)
    publishers = set(re.findall(r"\b(\w+)\s+\*?amqp\.Channel\b", declaration))
    if not publishers:
        return []
    parameter_types = _go_declared_parameter_types(declaration)
    contracts = []
    for node in _walk(function.body):
        if node.type != "call_expression":
            continue
        callee = node.child_by_field_name("function")
        arguments = node.child_by_field_name("arguments")
        if callee is None or arguments is None:
            continue
        callee_text = _text(callee, source)
        receiver, _separator, method = callee_text.rpartition(".")
        args = arguments.named_children
        positions = {"Publish": (0, 1, 4), "PublishWithContext": (1, 2, 5)}.get(method)
        if receiver not in publishers or positions is None or len(args) <= positions[2]:
            continue
        exchange = _string(args[positions[0]], source)
        routing_key = _string(args[positions[1]], source)
        if exchange is None or routing_key is None:
            continue
        payload = re.search(r"\bBody\s*:\s*(\w+)", _text(args[positions[2]], source))
        payload_type = parameter_types.get(payload.group(1)) if payload else None
        version = _message_header_version(_text(args[positions[2]], source))
        contracts.append(MessageContract("publishes", exchange, routing_key, payload_type, _evidence(path, root, node), version))
    return contracts


def _go_declared_parameter_types(declaration: str) -> dict[str, str]:
    parameters = re.search(r"func\s+(?:\([^)]*\)\s+)?\w+\s*\(([^)]*)\)", declaration, re.DOTALL)
    if parameters is None:
        return {}
    return {
        name: type_name
        for name, type_name in re.findall(r"\b(\w+)\s+(\*?[\w.\[\]]+)", parameters.group(1))
    }


def _message_header_version(source: str) -> str | None:
    header = r'["\']?(?:schema_version|schemaVersion|x-schema-version|x-version)["\']?'
    patterns = (
        rf"setHeader\s*\(\s*{header}\s*,\s*[\"']([^\"']+)",
        rf"{header}\s*:\s*[\"']([^\"']+)",
    )
    for pattern in patterns:
        if match := re.search(pattern, source):
            return match.group(1)
    return None


def _call_text(source: str, start_offset: int) -> str:
    opening = source.find("(", start_offset)
    if opening < 0:
        return source[start_offset:]
    depth = 0
    quote = ""
    for offset in range(opening, len(source)):
        char = source[offset]
        if quote:
            if char == quote and source[offset - 1] != "\\":
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return source[start_offset : offset + 1]
    return source[start_offset:]


def _spring_amqp_publishers(class_source: str) -> set[str]:
    java_fields = re.findall(r"\b(?:RabbitTemplate|AmqpTemplate)\s+(\w+)", class_source)
    kotlin_properties = re.findall(r"\b(?:val|var)\s+(\w+)\s*:\s*(?:RabbitTemplate|AmqpTemplate)", class_source)
    return set(java_fields) | set(kotlin_properties)


def _spring_publish_contracts(
    declaration: str,
    publishers: set[str],
    path: Path,
    root: Path,
    node: Node,
    *,
    kotlin: bool = False,
) -> list[MessageContract]:
    if not publishers:
        return []
    receivers = "|".join(re.escape(name) for name in sorted(publishers))
    pattern = rf'\b(?:{receivers})\s*\.\s*convertAndSend\s*\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*(\w+)'
    parameter_types = _declared_parameter_types(declaration, kotlin)
    contracts = []
    for match in re.finditer(pattern, declaration):
        exchange, routing_key, payload = match.groups()
        contracts.append(MessageContract(
            "publishes", exchange, routing_key, parameter_types.get(payload),
            _declaration_match_evidence(path, root, node, declaration, match.start(), match.end()),
            _message_header_version(_call_text(declaration, match.start())),
        ))
    return contracts


def _declared_parameter_types(declaration: str, kotlin: bool) -> dict[str, str]:
    parameters = re.search(r"\((.*?)\)", declaration, re.DOTALL)
    if parameters is None:
        return {}
    if kotlin:
        return {name: type_name.rstrip("?") for name, type_name in re.findall(r"(\w+)\s*:\s*([\w.<>?]+)", parameters.group(1))}
    return {
        name: type_name
        for type_name, name in re.findall(r"(?:@\w+\s+)*([\w<>?]+)\s+(\w+)", parameters.group(1))
    }


def _declaration_match_evidence(
    path: Path,
    root: Path,
    node: Node,
    declaration: str,
    start_offset: int,
    end_offset: int,
) -> Evidence:
    start_line = node.start_point.row + declaration.count("\n", 0, start_offset) + 1
    end_line = node.start_point.row + declaration.count("\n", 0, end_offset) + 1
    return Evidence(path.relative_to(root).as_posix(), start_line, end_line)


def _java_interfaces(class_text: str) -> tuple[str, ...]:
    match = re.search(r"\bimplements\s+([^\{]+)", class_text)
    return tuple(item.strip() for item in match.group(1).split(",")) if match else ()


def _spring_http_contract(declaration: str, annotations: str, kotlin: bool = False) -> dict:
    request = _spring_request(declaration, kotlin)
    return_type = _spring_return_type(declaration, kotlin)
    validations = re.findall(r"@(Valid|Validated|NotNull|NotBlank|NotEmpty|Positive|Negative|Size|Pattern)\b", declaration)
    authorization = re.findall(r"@(PreAuthorize|Secured|RolesAllowed)\b", annotations)
    contract = {
        "request": request,
        "returns": {"type": return_type, "required": True} if return_type else None,
        "validations": validations,
        "authorization": authorization,
        "parameters": _spring_bound_parameters(declaration, kotlin),
    }
    if statuses := _spring_response_statuses(f"{annotations}\n{declaration}"):
        contract["response_statuses"] = statuses
    return contract


_HTTP_STATUS_CODES = {"OK": 200, "CREATED": 201, "ACCEPTED": 202, "NO_CONTENT": 204, "BAD_REQUEST": 400, "NOT_FOUND": 404, "CONFLICT": 409}


def _spring_response_statuses(source: str) -> list[dict]:
    names = re.findall(r"(?:ResponseStatus|HttpStatus\.)\s*\(?\s*HttpStatus\.([A-Z_]+)", source)
    names += re.findall(r"HttpStatus\.([A-Z_]+)", source)
    return _status_values(names)


def _go_response_statuses(source: str) -> list[dict]:
    names = re.findall(r"WriteHeader\s*\(\s*http\.Status([A-Za-z]+)\s*\)", source)
    normalized = [re.sub(r"(?<!^)([A-Z])", r"_\1", name).upper() for name in names]
    return _status_values(normalized)


def _status_values(names: list[str]) -> list[dict]:
    return [{"code": _HTTP_STATUS_CODES[name], "name": name} for name in dict.fromkeys(names) if name in _HTTP_STATUS_CODES]


def _spring_bound_parameters(declaration: str, kotlin: bool) -> list[dict]:
    bindings = []
    for annotation, kind in (("PathVariable", "path"), ("RequestParam", "query"), ("RequestHeader", "header")):
        pattern = rf'@{annotation}\s*\(\s*(?:value\s*=\s*)?"([^"]+)"[^)]*\)\s+([\w<>?]+)\s+(\w+)'
        for name, type_name, variable in re.findall(pattern, declaration):
            bindings.append({"kind": kind, "name": name, "variable": variable, "type": type_name.rstrip("?"), "required": kind == "path"})
    return bindings


def _go_bound_parameters(declaration: str) -> list[dict]:
    bindings = []
    for pattern, kind in ((r'\.PathValue\s*\(\s*"([^"]+)"', "path"), (r'\.Query\s*\(\s*\)\.Get\s*\(\s*"([^"]+)"', "query"), (r'\.Header\.Get\s*\(\s*"([^"]+)"', "header")):
        bindings.extend({"kind": kind, "name": name, "variable": None, "type": None, "required": kind == "path"} for name in re.findall(pattern, declaration))
    return bindings


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


def _spring_route_prefix(annotations: str) -> str | None:
    match = re.search(r'@RequestMapping\s*\(\s*(?:value\s*=\s*)?"([^"]+)"', annotations)
    return match.group(1) if match else None


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


def _go_route_groups(tree: Node, source: bytes) -> dict[str, str]:
    groups = {}
    for node in _walk(tree):
        if node.type != "short_var_declaration":
            continue
        match = re.search(r'(\w+)\s*:=\s*\w+\.Group\s*\(\s*"([^"]+)"', _text(node, source))
        if match:
            groups[match.group(1)] = match.group(2)
    return groups


def _join_route(prefix: str | None, route: str | None) -> str | None:
    if route is None or prefix is None:
        return route
    return f"{prefix.rstrip('/')}/{route.lstrip('/')}"


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
            "node-ts": (_NodeGraphqlAnalyzer(Language(tree_sitter_typescript.language_typescript())), ("*.ts", "*.tsx", "*.graphql", "*.gql", "*.prisma")),
            "node-js": (_NodeGraphqlAnalyzer(Language(tree_sitter_javascript.language())), ("*.js", "*.jsx", "*.graphql", "*.gql", "*.prisma")),
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
        if stack in {"node-ts", "node-js"}:
            schema = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in files if path.suffix in {".graphql", ".gql"})
            result.contracts.update(_GraphqlContractExtractor().contracts(schema))
        if stack == "jvm-spring":
            result.edges = _classify_spring_data_derived_operations(
                result.edges, result.injections, _spring_data_repository_types(files),
            )
            result.edges = _classify_spring_data_query_operations(
                result.edges, result.injections, _spring_data_query_methods(files),
            )
        _enrich_contract_fields(result.contracts, files)
        _enrich_rabbitmq_contracts(result.contracts, files)
        _extract_scheduled_jobs(result, files, root)
        result.persistence_facts.extend(_persistence_facts(files, root))
        result = BoundedFlowResolver().resolve(result)
        result.edges.extend(self._depth_provider.enrich(root, result))
        return result


def _extract_scheduled_jobs(result: AnalysisResult, files: list[Path], root: Path) -> None:
    """Add JVM scheduled entrypoints only when annotation and method are explicit."""
    pattern = re.compile(
        r'@Scheduled\s*\(\s*(?:cron\s*=\s*)?["\']([^"\']+)["\'][^)]*\)\s*'
        r'(?:public\s+|private\s+|protected\s+)?(?:fun\s+)?[\w<>?\[\]\s]+\s+(\w+)\s*\(', re.MULTILINE,
    )
    for path in files:
        if path.suffix not in {".java", ".kt"}:
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        for match in pattern.finditer(source):
            method = match.group(2)
            candidates = [symbol for symbol in result.symbols if symbol.name.endswith(f".{method}") and symbol.evidence.file_path == path.relative_to(root).as_posix()]
            if len(candidates) != 1:
                continue
            symbol = candidates[0]
            evidence = Evidence(path.relative_to(root).as_posix(), source.count("\n", 0, match.start()) + 1, source.count("\n", 0, match.end()) + 1)
            result.entrypoints.append(EntryPoint("job", "SCHEDULED", method, symbol.name, evidence))
            result.contracts[symbol.name] = {"schedule": match.group(1), "concurrency": "unknown", "idempotency": "unknown"}


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


def _enrich_rabbitmq_contracts(contracts: dict[str, dict], files: list[Path]) -> None:
    queues = _rabbitmq_queue_options(files)
    bindings = _rabbitmq_bindings(files)
    source_text = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in files)
    for contract in contracts.values():
        if contract.get("transport") != "rabbitmq" or contract.get("direction") != "consumes":
            continue
        if options := queues.get(contract["queue"]):
            contract.update(options)
        if queue_bindings := bindings.get(contract["queue"]):
            contract["bindings"] = queue_bindings
        if re.search(r"\bidempot(?:ent|ency)\b", source_text, re.I):
            contract["idempotency"] = "detected"
        if re.search(r"\btimeout\b", source_text, re.I):
            contract["timeout"] = "detected"


def _rabbitmq_queue_options(files: list[Path]) -> dict[str, dict]:
    options = {}
    for path in files:
        source = path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r'(?:assertQueue|durable)\s*\(\s*"([^"]+)"', source):
            queue = match.group(1)
            declaration = source[match.start() : match.start() + 600]
            values = _rabbitmq_option_values(declaration)
            if values:
                options[queue] = values
        for queue, values in _go_rabbitmq_queue_options(source).items():
            options.setdefault(queue, {}).update(values)
    return options


def _rabbitmq_option_values(source: str) -> dict:
    dead_letter = re.search(r'(?:deadLetterRoutingKey\s*\(\s*|deadLetterRoutingKey|x-dead-letter-routing-key)\s*["\':=]+\s*"([^"]+)"', source)
    retry = re.search(r'(?:messageTtl\s*\(\s*|messageTtl|x-message-ttl)\s*["\':=]+\s*(\d+)', source)
    values = {}
    if dead_letter:
        values["dead_letter_routing_key"] = dead_letter.group(1)
    if retry:
        values["retry_delay_ms"] = int(retry.group(1))
    return values


def _rabbitmq_bindings(files: list[Path]) -> dict[str, list[dict]]:
    bindings: dict[str, set[tuple[str, str]]] = {}
    for path in files:
        source = path.read_text(encoding="utf-8", errors="ignore")
        for queue, exchange, routing_key in _node_rabbitmq_bindings(source):
            bindings.setdefault(queue, set()).add((exchange, routing_key))
        for queue, exchange, routing_key in _spring_rabbitmq_bindings(source):
            bindings.setdefault(queue, set()).add((exchange, routing_key))
        for queue, exchange, routing_key in _go_rabbitmq_bindings(source):
            bindings.setdefault(queue, set()).add((exchange, routing_key))
    return {
        queue: [{"exchange": exchange, "routing_key": routing_key} for exchange, routing_key in sorted(values)]
        for queue, values in bindings.items()
    }


def _node_rabbitmq_bindings(source: str) -> list[tuple[str, str, str]]:
    return re.findall(
        r'(?:\w+\.)?bindQueue\s*\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"', source,
    )


def _spring_rabbitmq_bindings(source: str) -> list[tuple[str, str, str]]:
    queues = _spring_rabbitmq_factories(source, "Queue")
    exchanges = _spring_rabbitmq_factories(source, r"(?:Topic|Direct|Fanout|Headers)?Exchange")
    bindings = []
    pattern = (
        r'BindingBuilder\s*\.\s*bind\s*\(\s*(\w+)\s*\(\s*\)\s*\)\s*'
        r'\.to\s*\(\s*(\w+)\s*\(\s*\)\s*\)\s*\.with\s*\(\s*"([^"]+)"'
    )
    for queue_factory, exchange_factory, routing_key in re.findall(pattern, source):
        queue = queues.get(queue_factory)
        exchange = exchanges.get(exchange_factory)
        if queue and exchange:
            bindings.append((queue, exchange, routing_key))
    return bindings


def _go_rabbitmq_bindings(source: str) -> list[tuple[str, str, str]]:
    channels = set(re.findall(r"\b(\w+)\s+\*?amqp\.Channel\b", source))
    pattern = r'(\w+)\.QueueBind\s*\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"'
    return [
        (queue, exchange, routing_key)
        for receiver, queue, routing_key, exchange in re.findall(pattern, source)
        if receiver in channels
    ]


def _go_rabbitmq_queue_options(source: str) -> dict[str, dict]:
    channels = set(re.findall(r"\b(\w+)\s+\*?amqp\.Channel\b", source))
    pattern = r'(\w+)\.QueueDeclare\s*\(\s*"([^"]+)"\s*,.*?amqp\.Table\s*\{(.*?)\}\s*\)'
    return {
        queue: values
        for receiver, queue, table in re.findall(pattern, source, re.DOTALL)
        if receiver in channels and (values := _rabbitmq_option_values(table))
    }


def _spring_rabbitmq_factories(source: str, type_pattern: str) -> dict[str, str]:
    java_pattern = rf'\b(\w+)\s*\([^)]*\)\s*\{{\s*return\s+new\s+(?:\w+\.)?{type_pattern}\s*\(\s*"([^"]+)"'
    kotlin_pattern = rf'\bfun\s+(\w+)\s*\([^)]*\)\s*(?::\s*[\w.<>?]+\s*)?=\s*(?:\w+\.)?{type_pattern}\s*\(\s*"([^"]+)"'
    return {name: value for name, value in re.findall(java_pattern, source) + re.findall(kotlin_pattern, source)}


def _persistence_facts(files: list[Path], root: Path) -> list[PersistenceFact]:
    facts = []
    for path in files:
        source = path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r"@Entity\s+(?:@Table\s*\(\s*name\s*=\s*\"([^\"]+)\"\s*\)\s*)?(?:class|data\s+class)\s+(\w+)", source):
            name, owner = match.group(1) or match.group(2), match.group(2)
            facts.append(PersistenceFact(name, "sql_table", owner, _line_evidence(path, root, source, match.start())))
        for match in re.finditer(r'@Document\s*\(\s*(?:collection\s*=\s*)?["\']([^"\']+)["\']\s*\)\s*(?:data\s+)?class\s+(\w+)', source):
            collection, owner = match.groups()
            facts.append(PersistenceFact(collection, "document", owner, _line_evidence(path, root, source, match.start())))
        for match in re.finditer(r"type\s+(\w+)\s+struct\s*\{(.*?)\}", source, re.DOTALL):
            if 'gorm:"' in match.group(2):
                facts.append(PersistenceFact(match.group(1), "sql_table", match.group(1), _line_evidence(path, root, source, match.start())))
        for match in re.finditer(r'\bmongoose\.model\s*(?:<[^>]+>)?\s*\(\s*["\']([^"\']+)["\']\s*,\s*[^,]+,\s*["\']([^"\']+)["\']', source):
            owner, collection = match.groups()
            facts.append(PersistenceFact(collection, "document", owner, _line_evidence(path, root, source, match.start())))
        facts.extend(_prisma_persistence_facts(source, path, root))
    return facts


def _prisma_persistence_facts(source: str, path: Path, root: Path) -> list[PersistenceFact]:
    providers = set(re.findall(r'datasource\s+\w+\s*\{[^}]*\bprovider\s*=\s*"([^"]+)"', source, re.DOTALL))
    kinds = {
        "postgresql": "sql_table", "mysql": "sql_table", "sqlite": "sql_table",
        "sqlserver": "sql_table", "cockroachdb": "sql_table", "mongodb": "document",
    }
    if len(providers) != 1 or (kind := kinds.get(next(iter(providers)))) is None:
        return []
    facts = []
    for match in re.finditer(r'model\s+(\w+)\s*\{(.*?)\}', source, re.DOTALL):
        owner, body = match.groups()
        mapping = re.search(r'@@map\s*\(\s*"([^"]+)"\s*\)', body)
        if mapping:
            facts.append(PersistenceFact(mapping.group(1), kind, owner, _line_evidence(path, root, source, match.start())))
    return facts


def _line_evidence(path: Path, root: Path, source: str, offset: int) -> Evidence:
    line = source.count("\n", 0, offset) + 1
    return Evidence(path.relative_to(root).as_posix(), line, line)


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
