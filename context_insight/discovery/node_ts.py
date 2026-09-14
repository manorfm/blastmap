from __future__ import annotations

import json
import re
from pathlib import Path

from context_insight.discovery.base import (
    CodeExcerpt,
    EndpointHint,
    MessagingHint,
    OutboundCallHint,
    PersistenceHint,
    ServiceHints,
)
from context_insight.discovery.scan_helpers import (
    ENDPOINT_AFTER,
    ENDPOINT_BEFORE,
    excerpt_around,
    find_matches,
    first_existing_file,
)

EXTENSIONS = (".js", ".ts")

_HTTP_METHOD_RE = re.compile(
    r"""\b(?:app|router)\.(get|post|put|patch|delete)\s*\(\s*['"`]([^'"`]+)['"`]""",
    re.IGNORECASE,
)
_NEST_ROUTE_RE = re.compile(r"""@(Get|Post|Put|Patch|Delete)\s*\(\s*['"`]?([^'")\`]*)['"`]?\s*\)""")
_NEST_CONTROLLER_RE = re.compile(r"""@Controller\s*\(\s*['"`]([^'"`]*)['"`]\s*\)""")

_OUTBOUND_RE = re.compile(
    r"\b(axios\.(?:get|post|put|patch|delete)|fetch|got)\s*\(\s*['\"`]?([^'\"`)]*)"
)
_GRPC_CLIENT_RE = re.compile(r"new\s+\w*(?:Client|Stub)\s*\(")
_QUEUE_PUBLISH_RE = re.compile(
    r"\b(producer\.send|channel\.publish|client\.emit|\.emit)\s*\(\s*['\"`]?([^'\"`,)]*)"
)
_QUEUE_CONSUME_RE = re.compile(
    r"\b(consumer\.subscribe|channel\.consume|@EventPattern|@MessagePattern)\s*\(\s*['\"`]?([^'\"`,)]*)"
)

_ENTITY_RE = re.compile(r"@Entity\s*\(\s*['\"`]?([^'\"`)]*)['\"`]?\s*\)")
_PRISMA_MODEL_RE = re.compile(r"^model\s+(\w+)\s*\{", re.MULTILINE)
_MONGOOSE_SCHEMA_RE = re.compile(r"new\s+(?:mongoose\.)?Schema\s*\(")
_SEQUELIZE_DEFINE_RE = re.compile(r"\.define\s*\(\s*['\"`]([^'\"`]+)['\"`]")


class NodeTsDetector:
    id = "node-ts"

    def matches(self, folder: Path) -> bool:
        pkg = folder / "package.json"
        if not pkg.is_file():
            return False
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError):
            return False
        scripts = data.get("scripts", {})
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        has_entry = first_existing_file(folder, ("main.ts", "app.ts", "server.js", "server.ts", "index.js", "index.ts"))
        return bool(scripts.get("start") or scripts.get("start:prod") or has_entry or "express" in deps or "@nestjs/core" in deps)

    def collect_hints(self, folder: Path) -> ServiceHints:
        hints = ServiceHints()

        entry = first_existing_file(folder, ("src/main.ts", "src/app.ts", "main.ts", "app.ts", "server.js", "index.js"))
        if entry:
            hints.entry_excerpt = excerpt_around(entry, folder, 1, context=20)

        for path, line_no, match in find_matches(folder, EXTENSIONS, _HTTP_METHOD_RE):
            method, route = match.group(1).upper(), match.group(2)
            excerpt = excerpt_around(path, folder, line_no, before=ENDPOINT_BEFORE, after=ENDPOINT_AFTER)
            hints.endpoints.append(EndpointHint(method=method, path=route, excerpt=excerpt))

        for path, line_no, match in find_matches(folder, EXTENSIONS, _NEST_ROUTE_RE):
            method, route = match.group(1).upper(), match.group(2) or "/"
            excerpt = excerpt_around(path, folder, line_no, before=ENDPOINT_BEFORE, after=ENDPOINT_AFTER)
            hints.endpoints.append(EndpointHint(method=method, path=route, excerpt=excerpt))

        for path, line_no, match in find_matches(folder, EXTENSIONS, _OUTBOUND_RE):
            hints.outbound_calls.append(
                OutboundCallHint(call_kind="http", target_hint=match.group(2), excerpt=excerpt_around(path, folder, line_no))
            )
        for path, line_no, match in find_matches(folder, EXTENSIONS, _GRPC_CLIENT_RE):
            hints.outbound_calls.append(
                OutboundCallHint(call_kind="grpc", target_hint=match.group(0), excerpt=excerpt_around(path, folder, line_no))
            )
        for path, line_no, match in find_matches(folder, EXTENSIONS, _QUEUE_PUBLISH_RE):
            hints.messaging.append(
                MessagingHint(direction="publishes", channel_hint=match.group(2) or "?", excerpt=excerpt_around(path, folder, line_no))
            )
        for path, line_no, match in find_matches(folder, EXTENSIONS, _QUEUE_CONSUME_RE):
            hints.messaging.append(
                MessagingHint(direction="consumes", channel_hint=match.group(2) or "?", excerpt=excerpt_around(path, folder, line_no))
            )

        for path, line_no, match in find_matches(folder, EXTENSIONS, _ENTITY_RE):
            hints.persistence.append(
                PersistenceHint(kind="sql_table", name_hint=match.group(1) or "?", excerpt=excerpt_around(path, folder, line_no))
            )
        for path, line_no, match in find_matches(folder, EXTENSIONS, _MONGOOSE_SCHEMA_RE):
            hints.persistence.append(
                PersistenceHint(kind="document", name_hint="?", excerpt=excerpt_around(path, folder, line_no))
            )
        for path, line_no, match in find_matches(folder, EXTENSIONS, _SEQUELIZE_DEFINE_RE):
            hints.persistence.append(
                PersistenceHint(kind="sql_table", name_hint=match.group(1), excerpt=excerpt_around(path, folder, line_no))
            )
        prisma_schema = folder / "prisma" / "schema.prisma"
        if prisma_schema.is_file():
            text = prisma_schema.read_text(encoding="utf-8", errors="ignore")
            for match in _PRISMA_MODEL_RE.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                hints.persistence.append(
                    PersistenceHint(kind="sql_table", name_hint=match.group(1), excerpt=excerpt_around(prisma_schema, folder, line_no))
                )

        return hints
