"""Shared types for stack detectors.

A detector only points at interesting code (a "hint"): a file, a line range, and a
short excerpt. It never tries to build a call graph or resolve what a hint means —
that synthesis is left entirely to the LLM generation step. This keeps each detector
small and mechanical (regex / file-signature matching).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class CodeExcerpt:
    file_path: str  # relative to service root, forward slashes
    start_line: int
    end_line: int
    text: str


@dataclass
class EndpointHint:
    method: str  # HTTP verb, or 'RPC' / 'GRAPHQL' / 'CONSUMER'
    path: str  # route path or rpc method name
    component_hint: str  # enclosing class name, or the file's stem when there is none
    excerpt: CodeExcerpt
    extra_excerpts: list[CodeExcerpt] = field(default_factory=list)

    def dependency_files(self) -> set[str]:
        return {self.excerpt.file_path, *(e.file_path for e in self.extra_excerpts)}


@dataclass
class OutboundCallHint:
    call_kind: str  # 'http' | 'grpc' | 'queue_publish' | 'queue_consume'
    target_hint: str  # best-effort guessed target name/url/topic, may be vague
    excerpt: CodeExcerpt


@dataclass
class PersistenceHint:
    kind: str  # 'sql_table' | 'document' | 'cache' | 'other'
    name_hint: str
    excerpt: CodeExcerpt


@dataclass
class MessagingHint:
    direction: str  # 'publishes' | 'consumes'
    channel_hint: str
    excerpt: CodeExcerpt


@dataclass
class ServiceHints:
    endpoints: list[EndpointHint] = field(default_factory=list)
    outbound_calls: list[OutboundCallHint] = field(default_factory=list)
    persistence: list[PersistenceHint] = field(default_factory=list)
    messaging: list[MessagingHint] = field(default_factory=list)
    entry_excerpt: CodeExcerpt | None = None

    def relevant_files(self) -> set[str]:
        files: set[str] = set()
        if self.entry_excerpt:
            files.add(self.entry_excerpt.file_path)
        for e in self.endpoints:
            files |= e.dependency_files()
        for c in self.outbound_calls:
            files.add(c.excerpt.file_path)
        for p in self.persistence:
            files.add(p.excerpt.file_path)
        for m in self.messaging:
            files.add(m.excerpt.file_path)
        return files


class StackDetector(Protocol):
    id: str

    def matches(self, folder: Path) -> bool:
        """Cheap signature check: does this folder look like the root of a service in this stack?"""
        ...

    def collect_hints(self, folder: Path) -> ServiceHints:
        """Scan the folder and return hints. Called only after matches() returned True."""
        ...
