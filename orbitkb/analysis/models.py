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


@dataclass(frozen=True)
class Symbol:
    """A locally declared callable used only for bounded flow resolution."""

    name: str
    owner: str
    member: str
    evidence: Evidence
    implements: tuple[str, ...] = ()
    imports: tuple[tuple[str, str], ...] = ()
    qualifiers: tuple[str, ...] = ()
    primary: bool = False


@dataclass(frozen=True)
class Injection:
    """A declared dependency selection used only for bounded resolution."""

    consumer: str
    contract: str
    qualifier: str | None
    evidence: Evidence


@dataclass(frozen=True)
class MessageContract:
    direction: str
    channel: str
    routing_key: str | None
    payload_type: str | None
    evidence: Evidence


@dataclass
class AnalysisResult:
    entrypoints: list[EntryPoint] = field(default_factory=list)
    edges: list[FlowEdge] = field(default_factory=list)
    contracts: dict[str, dict] = field(default_factory=dict)
    symbols: list[Symbol] = field(default_factory=list)
    injections: list[Injection] = field(default_factory=list)
    message_contracts: list[MessageContract] = field(default_factory=list)

    def extend(self, other: AnalysisResult) -> None:
        self.entrypoints.extend(other.entrypoints)
        self.edges.extend(other.edges)
        self.contracts.update(other.contracts)
        self.symbols.extend(other.symbols)
        self.injections.extend(other.injections)
        self.message_contracts.extend(other.message_contracts)
