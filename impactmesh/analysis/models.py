from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Evidence:
    file_path: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class EntryPoint:
    """A concrete way work enters a service, independent of transport."""

    kind: str
    method: str
    name: str
    symbol: str
    evidence: Evidence


@dataclass(frozen=True)
class FlowEdge:
    """A deterministic relation observed in a bounded entrypoint flow."""

    source: str
    target: str
    kind: str
    evidence: Evidence
    confidence: str = "high"
    origin: str = "static"


@dataclass
class AnalysisResult:
    entrypoints: list[EntryPoint] = field(default_factory=list)
    edges: list[FlowEdge] = field(default_factory=list)

    def extend(self, other: AnalysisResult) -> None:
        self.entrypoints.extend(other.entrypoints)
        self.edges.extend(other.edges)
