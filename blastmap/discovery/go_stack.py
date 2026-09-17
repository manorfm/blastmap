from __future__ import annotations

import re
from pathlib import Path

from blastmap.discovery.base import (
    EndpointHint,
    MessagingHint,
    OutboundCallHint,
    PersistenceHint,
    ServiceHints,
)
from blastmap.discovery.scan_helpers import (
    ENDPOINT_AFTER,
    ENDPOINT_BEFORE,
    excerpt_around,
    find_matches,
    first_existing_file,
)

EXTENSIONS = (".go",)

_ROUTER_RE = re.compile(
    r"\b(?:router|r|mux|e|app)\.(GET|POST|PUT|PATCH|DELETE|Handle)\s*\(\s*\"([^\"]+)\"",
)
_NET_HTTP_HANDLE_RE = re.compile(r"\bhttp\.HandleFunc\s*\(\s*\"([^\"]+)\"")
_GRPC_SERVER_METHOD_RE = re.compile(r"func\s+\(\w+\s+\*?\w*Server\)\s+(\w+)\s*\(")

_OUTBOUND_HTTP_RE = re.compile(r"\bhttp\.(Get|Post|NewRequest)\s*\(")
_GRPC_CLIENT_RE = re.compile(r"New\w*Client\s*\(\s*conn\s*\)")

_QUEUE_PUBLISH_RE = re.compile(r"\b(producer\.Produce|writer\.WriteMessages|ch\.Publish)\s*\(")
_QUEUE_CONSUME_RE = re.compile(r"\b(consumer\.Consume|reader\.ReadMessage|ch\.Consume)\s*\(")

_GORM_MODEL_RE = re.compile(r"type\s+(\w+)\s+struct\s*\{[^}]*gorm\.Model", re.DOTALL)
_SQL_QUERY_RE = re.compile(r"\bdb\.(Query|Exec|QueryRow)\s*\(\s*\"([^\"]*)")


class GoDetector:
    id = "go"

    def matches(self, folder: Path) -> bool:
        if not (folder / "go.mod").is_file():
            return False
        return first_existing_file(folder, ("main.go",)) is not None or any(folder.glob("cmd/*/main.go"))

    def collect_hints(self, folder: Path) -> ServiceHints:
        hints = ServiceHints()

        entry = first_existing_file(folder, ("main.go",))
        if entry is None:
            candidates = list(folder.glob("cmd/*/main.go"))
            entry = candidates[0] if candidates else None
        if entry:
            hints.entry_excerpt = excerpt_around(entry, folder, 1, context=20)

        for path, line_no, match in find_matches(folder, EXTENSIONS, _ROUTER_RE):
            method = match.group(1).upper()
            if method == "HANDLE":
                method = "GET"
            excerpt = excerpt_around(path, folder, line_no, before=ENDPOINT_BEFORE, after=ENDPOINT_AFTER)
            hints.endpoints.append(EndpointHint(method=method, path=match.group(2), excerpt=excerpt))
        for path, line_no, match in find_matches(folder, EXTENSIONS, _NET_HTTP_HANDLE_RE):
            excerpt = excerpt_around(path, folder, line_no, before=ENDPOINT_BEFORE, after=ENDPOINT_AFTER)
            hints.endpoints.append(EndpointHint(method="GET", path=match.group(1), excerpt=excerpt))
        for path, line_no, match in find_matches(folder, EXTENSIONS, _GRPC_SERVER_METHOD_RE):
            excerpt = excerpt_around(path, folder, line_no, before=ENDPOINT_BEFORE, after=ENDPOINT_AFTER)
            hints.endpoints.append(EndpointHint(method="RPC", path=match.group(1), excerpt=excerpt))

        for path, line_no, match in find_matches(folder, EXTENSIONS, _OUTBOUND_HTTP_RE):
            hints.outbound_calls.append(
                OutboundCallHint(call_kind="http", target_hint=match.group(1), excerpt=excerpt_around(path, folder, line_no))
            )
        for path, line_no, match in find_matches(folder, EXTENSIONS, _GRPC_CLIENT_RE):
            hints.outbound_calls.append(
                OutboundCallHint(call_kind="grpc", target_hint=match.group(0), excerpt=excerpt_around(path, folder, line_no))
            )

        for path, line_no, match in find_matches(folder, EXTENSIONS, _QUEUE_PUBLISH_RE):
            hints.messaging.append(
                MessagingHint(direction="publishes", channel_hint="?", excerpt=excerpt_around(path, folder, line_no))
            )
        for path, line_no, match in find_matches(folder, EXTENSIONS, _QUEUE_CONSUME_RE):
            hints.messaging.append(
                MessagingHint(direction="consumes", channel_hint="?", excerpt=excerpt_around(path, folder, line_no))
            )

        for path, line_no, match in find_matches(folder, EXTENSIONS, _GORM_MODEL_RE):
            hints.persistence.append(
                PersistenceHint(kind="sql_table", name_hint=match.group(1), excerpt=excerpt_around(path, folder, line_no))
            )
        for path, line_no, match in find_matches(folder, EXTENSIONS, _SQL_QUERY_RE):
            hints.persistence.append(
                PersistenceHint(kind="sql_table", name_hint="?", excerpt=excerpt_around(path, folder, line_no))
            )

        return hints
