"""Optional, bounded enrichment from an external code-intelligence MCP server."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
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


class NoopDepthProvider:
    def enrich(self, root: Path, analysis: AnalysisResult) -> list[FlowEdge]:
        return []


@dataclass(frozen=True)
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

    def enrich(self, root: Path, analysis: AnalysisResult) -> list[FlowEdge]:
        try:
            return asyncio.run(self._enrich(root, analysis))
        except Exception as error:
            if self.mode is DepthMode.REQUIRE:
                raise RuntimeError(f"depth provider failed: {error}") from error
            logger.warning("depth provider unavailable; using static flow only: %s", error)
            return []

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
) -> DepthProvider:
    if timeout_seconds <= 0:
        raise ValueError("--depth-timeout must be greater than zero")
    if max_edges < 1:
        raise ValueError("--depth-max-edges must be greater than zero")
    if mode is DepthMode.OFF or (mode is DepthMode.AUGMENT and not command):
        return NoopDepthProvider()
    if not command:
        raise ValueError("depth mode 'require' requires --depth-command")
    return McpDepthProvider(command, args, tool_name, mode, timeout_seconds, max_edges)
