"""Optional, bounded enrichment from an external code-intelligence MCP server."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from orbitkb.analysis.models import AnalysisResult, Evidence, FlowEdge

logger = logging.getLogger(__name__)


class DepthMode(StrEnum):
    OFF = "off"
    AUGMENT = "augment"
    REQUIRE = "require"


class DepthProvider(Protocol):
    """Enriches selected entrypoint flows; it never owns service discovery."""

    def enrich(self, root: Path, analysis: AnalysisResult) -> list[FlowEdge]: ...
    def metrics(self) -> dict: ...


class NoopDepthProvider:
    def enrich(self, root: Path, analysis: AnalysisResult) -> list[FlowEdge]:
        return []

    def metrics(self) -> dict:
        return {"enabled": False, "calls": 0, "cache_hits": 0, "failures": 0, "circuit_open": False}


@dataclass
class McpDepthProvider:
    """Adapter for a configured code-intelligence MCP process.

    The external tool is intentionally small and explicit: it receives a repository
    path and one entrypoint symbol, then returns ``{"edges": [...]}``. Each edge
    must provide ``from``, ``to``, ``kind`` and ``evidence``. This prevents a plugin
    from injecting an opaque whole-repository graph into OrbitKB.
    """

    command: str
    args: tuple[str, ...]
    tool_name: str
    mode: DepthMode
    timeout_seconds: float = 15.0
    max_edges: int = 100
    cache_entries: int = 128
    circuit_failure_threshold: int = 3
    circuit_cooldown_seconds: float = 30.0
    _cache: dict[tuple[str, tuple[str, ...]], list[FlowEdge]] = field(default_factory=dict, init=False, repr=False)
    _calls: int = field(default=0, init=False, repr=False)
    _cache_hits: int = field(default=0, init=False, repr=False)
    _failures: int = field(default=0, init=False, repr=False)
    _circuit_opened_at: float | None = field(default=None, init=False, repr=False)
    _latency_ms_total: float = field(default=0, init=False, repr=False)

    def enrich(self, root: Path, analysis: AnalysisResult) -> list[FlowEdge]:
        if self._circuit_is_open():
            return []
        key = (str(root.resolve()), tuple(dict.fromkeys(item.symbol for item in analysis.entrypoints)))
        if key in self._cache:
            self._cache_hits += 1
            return list(self._cache[key])
        started_at = time.monotonic()
        self._calls += 1
        try:
            edges = asyncio.run(self._enrich(root, analysis))
            self._latency_ms_total += (time.monotonic() - started_at) * 1000
            self._failures = 0
            if len(self._cache) >= self.cache_entries:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = list(edges)
            return edges
        except Exception:
            self._latency_ms_total += (time.monotonic() - started_at) * 1000
            self._failures += 1
            if self._failures >= self.circuit_failure_threshold:
                self._circuit_opened_at = time.monotonic()
            if self.mode is DepthMode.REQUIRE:
                raise RuntimeError("depth provider failed")
            logger.warning("depth provider unavailable; using static flow only")
            return []

    def _circuit_is_open(self) -> bool:
        if self._circuit_opened_at is None:
            return False
        if time.monotonic() - self._circuit_opened_at < self.circuit_cooldown_seconds:
            return True
        self._circuit_opened_at = None
        self._failures = 0
        return False

    def metrics(self) -> dict:
        return {
            "enabled": True, "calls": self._calls, "cache_hits": self._cache_hits,
            "failures": self._failures, "circuit_open": self._circuit_is_open(),
            "average_latency_ms": round(self._latency_ms_total / self._calls, 2) if self._calls else None,
        }

    async def _enrich(self, root: Path, analysis: AnalysisResult) -> list[FlowEdge]:
        params = StdioServerParameters(command=self.command, args=list(self.args))
        edges: list[FlowEdge] = []
        symbols = list(dict.fromkeys(entrypoint.symbol for entrypoint in analysis.entrypoints))
        async with asyncio.timeout(self.timeout_seconds):
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                for symbol in symbols:
                    if len(edges) >= self.max_edges:
                        break
                    result = await session.call_tool(
                        self.tool_name,
                        {"repository": str(root), "symbol": symbol},
                    )
                    edges.extend(_parse_edges(result, symbol, max_edges=self.max_edges - len(edges)))
        return edges


def _parse_edges(result: object, default_source: str, max_edges: int = 100) -> list[FlowEdge]:
    payload = result if isinstance(result, dict) else getattr(result, "structuredContent", None)
    if payload is None:
        for content in getattr(result, "content", []):
            text = getattr(content, "text", None)
            if text:
                payload = json.loads(text)
                break
    if not isinstance(payload, dict):
        return []
    edges = []
    for item in payload.get("edges", []):
        if len(edges) >= max_edges:
            break
        if not isinstance(item, dict) or not isinstance(item.get("to"), str):
            continue
        evidence = item.get("evidence") or {}
        if not isinstance(evidence, dict):
            evidence = {}
        kind = item.get("kind", "invokes")
        if kind not in {"invokes", "injects", "validates", "reads", "writes", "publishes", "consumes"}:
            continue
        edges.append(
            FlowEdge(
                source=item.get("from", default_source),
                target=item["to"],
                kind=kind,
                confidence=item.get("confidence", "medium"),
                origin="codegraph",
                evidence=Evidence(evidence.get("file", "<codegraph>"), evidence.get("start_line", 0), evidence.get("end_line", 0)),
            )
        )
    return edges


def resolve_depth_provider(
    mode: DepthMode,
    command: str | None,
    args: tuple[str, ...],
    tool_name: str,
    timeout_seconds: float = 15.0,
    max_edges: int = 100,
    cache_entries: int = 128,
    circuit_failure_threshold: int = 3,
    circuit_cooldown_seconds: float = 30.0,
) -> DepthProvider:
    if timeout_seconds <= 0:
        raise ValueError("--depth-timeout must be greater than zero")
    if max_edges < 1:
        raise ValueError("--depth-max-edges must be greater than zero")
    if cache_entries < 1 or circuit_failure_threshold < 1 or circuit_cooldown_seconds <= 0:
        raise ValueError("depth cache and circuit limits must be greater than zero")
    if mode is DepthMode.OFF or (mode is DepthMode.AUGMENT and not command):
        return NoopDepthProvider()
    if not command:
        raise ValueError("depth mode 'require' requires --depth-command")
    return McpDepthProvider(
        command, args, tool_name, mode, timeout_seconds, max_edges,
        cache_entries, circuit_failure_threshold, circuit_cooldown_seconds,
    )
