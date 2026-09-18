from __future__ import annotations

import re
from pathlib import Path

from context_insight.discovery.base import (
    EndpointHint,
    MessagingHint,
    OutboundCallHint,
    PersistenceHint,
    ServiceHints,
)
from context_insight.discovery.scan_helpers import (
    ENDPOINT_AFTER,
    ENDPOINT_BEFORE,
    component_hint_for,
    excerpt_around,
    find_matches,
    first_existing_file,
    resolve_local_calls,
)

EXTENSIONS = (".py",)

_FASTAPI_ROUTE_RE = re.compile(
    r"@(?:app|router|\w+_router)\.(get|post|put|patch|delete)\s*\(\s*['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)
_FLASK_ROUTE_RE = re.compile(
    r"@(?:app|\w+_bp|blueprint)\.route\s*\(\s*['\"]([^'\"]+)['\"](?:.*?methods\s*=\s*\[([^\]]*)\])?",
    re.DOTALL,
)
_DJANGO_URL_RE = re.compile(r"\b(?:path|re_path)\s*\(\s*r?['\"]([^'\"]*)['\"]")

_OUTBOUND_RE = re.compile(r"\b(requests\.(?:get|post|put|patch|delete)|httpx\.\w+\.(?:get|post|put|patch|delete))\s*\(\s*['\"]?([^'\")]*)")
_GRPC_STUB_RE = re.compile(r"(\w*Stub)\s*\(\s*channel\s*\)")
_CELERY_DELAY_RE = re.compile(r"\b(\w+)\.(?:delay|apply_async)\s*\(")

_QUEUE_PUBLISH_RE = re.compile(r"\b(producer\.send|channel\.basic_publish)\s*\(")
_QUEUE_CONSUME_RE = re.compile(r"\b(consumer\.subscribe|channel\.basic_consume|@\w+\.task)\b")

_SQLALCHEMY_MODEL_RE = re.compile(r"class\s+(\w+)\s*\([^)]*Base[^)]*\)\s*:")
_DJANGO_MODEL_RE = re.compile(r"class\s+(\w+)\s*\(\s*models\.Model\s*\)\s*:")

_CLASS_RE = re.compile(r"^\s*class\s+(\w+)")


def _def_pattern(name: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*def\s+{re.escape(name)}\s*\(", re.MULTILINE)


def _endpoint_hint(method: str, path_value: str, file_path: Path, folder: Path, line_no: int) -> EndpointHint:
    excerpt = excerpt_around(file_path, folder, line_no, before=ENDPOINT_BEFORE, after=ENDPOINT_AFTER)
    component_hint = component_hint_for(file_path, line_no, _CLASS_RE)
    extra_excerpts = resolve_local_calls(
        file_path, folder, excerpt.text, _def_pattern, (excerpt.start_line, excerpt.end_line),
    )
    return EndpointHint(
        method=method, path=path_value, component_hint=component_hint,
        excerpt=excerpt, extra_excerpts=extra_excerpts,
    )


class PythonDetector:
    id = "python"

    def matches(self, folder: Path) -> bool:
        has_manifest = first_existing_file(folder, ("requirements.txt", "pyproject.toml", "Pipfile"))
        has_entry = first_existing_file(folder, ("main.py", "app.py", "wsgi.py", "asgi.py", "manage.py"))
        return bool(has_manifest and has_entry) or (folder / "manage.py").is_file()

    def collect_hints(self, folder: Path) -> ServiceHints:
        hints = ServiceHints()

        entry = first_existing_file(folder, ("main.py", "app.py", "asgi.py", "wsgi.py", "manage.py"))
        if entry:
            hints.entry_excerpt = excerpt_around(entry, folder, 1, context=20)

        for path, line_no, match in find_matches(folder, EXTENSIONS, _FASTAPI_ROUTE_RE):
            hints.endpoints.append(_endpoint_hint(match.group(1).upper(), match.group(2), path, folder, line_no))
        for path, line_no, match in find_matches(folder, EXTENSIONS, _FLASK_ROUTE_RE):
            methods = match.group(2)
            method = methods.split(",")[0].strip(" '\"").upper() if methods else "GET"
            hints.endpoints.append(_endpoint_hint(method, match.group(1), path, folder, line_no))
        urls_file = folder / "urls.py"
        if urls_file.is_file():
            for path, line_no, match in find_matches(folder, EXTENSIONS, _DJANGO_URL_RE):
                if path.name != "urls.py":
                    continue
                hints.endpoints.append(_endpoint_hint("GET", match.group(1) or "/", path, folder, line_no))

        for path, line_no, match in find_matches(folder, EXTENSIONS, _OUTBOUND_RE):
            hints.outbound_calls.append(
                OutboundCallHint(call_kind="http", target_hint=match.group(2), excerpt=excerpt_around(path, folder, line_no))
            )
        for path, line_no, match in find_matches(folder, EXTENSIONS, _GRPC_STUB_RE):
            hints.outbound_calls.append(
                OutboundCallHint(call_kind="grpc", target_hint=match.group(1), excerpt=excerpt_around(path, folder, line_no))
            )
        for path, line_no, match in find_matches(folder, EXTENSIONS, _CELERY_DELAY_RE):
            hints.outbound_calls.append(
                OutboundCallHint(call_kind="queue_publish", target_hint=match.group(1), excerpt=excerpt_around(path, folder, line_no))
            )

        for path, line_no, match in find_matches(folder, EXTENSIONS, _QUEUE_PUBLISH_RE):
            hints.messaging.append(
                MessagingHint(direction="publishes", channel_hint="?", excerpt=excerpt_around(path, folder, line_no))
            )
        for path, line_no, match in find_matches(folder, EXTENSIONS, _QUEUE_CONSUME_RE):
            hints.messaging.append(
                MessagingHint(direction="consumes", channel_hint="?", excerpt=excerpt_around(path, folder, line_no))
            )

        for path, line_no, match in find_matches(folder, EXTENSIONS, _SQLALCHEMY_MODEL_RE):
            hints.persistence.append(
                PersistenceHint(kind="sql_table", name_hint=match.group(1), excerpt=excerpt_around(path, folder, line_no))
            )
        for path, line_no, match in find_matches(folder, EXTENSIONS, _DJANGO_MODEL_RE):
            hints.persistence.append(
                PersistenceHint(kind="sql_table", name_hint=match.group(1), excerpt=excerpt_around(path, folder, line_no))
            )

        return hints
