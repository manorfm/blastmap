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

EXTENSIONS = (".java", ".kt")

_MAPPING_RE = re.compile(
    r"@(GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping|RequestMapping)"
    r"\s*\(\s*(?:value\s*=\s*)?['\"]?([^'\")]*)['\"]?\s*\)?"
)
_REST_CONTROLLER_RE = re.compile(r"@RestController")

_METHOD_BY_ANNOTATION = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "PatchMapping": "PATCH",
    "DeleteMapping": "DELETE",
    "RequestMapping": "REQUEST",
}

_OUTBOUND_RE = re.compile(r"\b(RestTemplate|WebClient)\b.*?\.(get|post|put|patch|delete|exchange)\s*\(", re.DOTALL)
_FEIGN_CLIENT_RE = re.compile(r"@FeignClient\s*\(\s*(?:name\s*=\s*)?['\"]?([^'\")]*)['\"]?")
_GRPC_STUB_RE = re.compile(r"(\w*Grpc\.\w*Stub)\s+\w+")
_STREAM_SEND_RE = re.compile(r"\bStreamBridge\.\w*send\s*\(\s*['\"]?([^'\")]*)")

_KAFKA_LISTENER_RE = re.compile(r"@KafkaListener\s*\(\s*(?:topics\s*=\s*)?['\"]?([^'\")]*)['\"]?")
_KAFKA_SEND_RE = re.compile(r"\bKafkaTemplate\b.*?\.send\s*\(\s*['\"]?([^'\")]*)", re.DOTALL)
_RABBIT_LISTENER_RE = re.compile(r"@RabbitListener\s*\(\s*(?:queues\s*=\s*)?['\"]?([^'\")]*)['\"]?")
_RABBIT_SEND_RE = re.compile(r"\bRabbitTemplate\b.*?\.convertAndSend\s*\(\s*['\"]?([^'\")]*)", re.DOTALL)

_JPA_ENTITY_RE = re.compile(r"@Entity\b.*?\bclass\s+(\w+)", re.DOTALL)
_SPRING_DATA_REPO_RE = re.compile(r"interface\s+(\w+)\s+extends\s+\w*Repository")


def _has_spring_boot_dependency(folder: Path) -> bool:
    pom = folder / "pom.xml"
    if pom.is_file() and "spring-boot" in pom.read_text(encoding="utf-8", errors="ignore"):
        return True
    for gradle_name in ("build.gradle", "build.gradle.kts"):
        gradle = folder / gradle_name
        if gradle.is_file() and "spring-boot" in gradle.read_text(encoding="utf-8", errors="ignore"):
            return True
    return False


class JvmSpringDetector:
    id = "jvm-spring"

    def matches(self, folder: Path) -> bool:
        has_build_file = (folder / "pom.xml").is_file() or (folder / "build.gradle").is_file() or (folder / "build.gradle.kts").is_file()
        if not has_build_file:
            return False
        if _has_spring_boot_dependency(folder):
            return True
        src = folder / "src" / "main"
        if src.is_dir():
            for path, _line_no, _m in find_matches(src, EXTENSIONS, re.compile(r"@SpringBootApplication")):
                return True
        return False

    def collect_hints(self, folder: Path) -> ServiceHints:
        hints = ServiceHints()
        src = folder / "src" / "main"
        scan_root = src if src.is_dir() else folder

        entry_matches = find_matches(scan_root, EXTENSIONS, re.compile(r"@SpringBootApplication"))
        if entry_matches:
            path, line_no, _m = entry_matches[0]
            hints.entry_excerpt = excerpt_around(path, folder, line_no, context=20)

        for path, line_no, match in find_matches(scan_root, EXTENSIONS, _MAPPING_RE):
            annotation, route = match.group(1), match.group(2) or "/"
            excerpt = excerpt_around(path, folder, line_no, before=ENDPOINT_BEFORE, after=ENDPOINT_AFTER)
            hints.endpoints.append(
                EndpointHint(method=_METHOD_BY_ANNOTATION[annotation], path=route, excerpt=excerpt)
            )

        for path, line_no, match in find_matches(scan_root, EXTENSIONS, _OUTBOUND_RE):
            hints.outbound_calls.append(
                OutboundCallHint(call_kind="http", target_hint=match.group(1), excerpt=excerpt_around(path, folder, line_no))
            )
        for path, line_no, match in find_matches(scan_root, EXTENSIONS, _FEIGN_CLIENT_RE):
            hints.outbound_calls.append(
                OutboundCallHint(call_kind="http", target_hint=match.group(1) or "?", excerpt=excerpt_around(path, folder, line_no))
            )
        for path, line_no, match in find_matches(scan_root, EXTENSIONS, _GRPC_STUB_RE):
            hints.outbound_calls.append(
                OutboundCallHint(call_kind="grpc", target_hint=match.group(1), excerpt=excerpt_around(path, folder, line_no))
            )
        for path, line_no, match in find_matches(scan_root, EXTENSIONS, _STREAM_SEND_RE):
            hints.messaging.append(
                MessagingHint(direction="publishes", channel_hint=match.group(1) or "?", excerpt=excerpt_around(path, folder, line_no))
            )

        for path, line_no, match in find_matches(scan_root, EXTENSIONS, _KAFKA_LISTENER_RE):
            hints.messaging.append(
                MessagingHint(direction="consumes", channel_hint=match.group(1) or "?", excerpt=excerpt_around(path, folder, line_no))
            )
        for path, line_no, match in find_matches(scan_root, EXTENSIONS, _KAFKA_SEND_RE):
            hints.messaging.append(
                MessagingHint(direction="publishes", channel_hint=match.group(1) or "?", excerpt=excerpt_around(path, folder, line_no))
            )
        for path, line_no, match in find_matches(scan_root, EXTENSIONS, _RABBIT_LISTENER_RE):
            hints.messaging.append(
                MessagingHint(direction="consumes", channel_hint=match.group(1) or "?", excerpt=excerpt_around(path, folder, line_no))
            )
        for path, line_no, match in find_matches(scan_root, EXTENSIONS, _RABBIT_SEND_RE):
            hints.messaging.append(
                MessagingHint(direction="publishes", channel_hint=match.group(1) or "?", excerpt=excerpt_around(path, folder, line_no))
            )

        for path, line_no, match in find_matches(scan_root, EXTENSIONS, _JPA_ENTITY_RE):
            hints.persistence.append(
                PersistenceHint(kind="sql_table", name_hint=match.group(1), excerpt=excerpt_around(path, folder, line_no))
            )
        for path, line_no, match in find_matches(scan_root, EXTENSIONS, _SPRING_DATA_REPO_RE):
            hints.persistence.append(
                PersistenceHint(kind="sql_table", name_hint=match.group(1), excerpt=excerpt_around(path, folder, line_no))
            )

        return hints
