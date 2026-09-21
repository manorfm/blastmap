"""Bounded symbol resolution for locally extracted flows.

This is deliberately not a repository-wide graph. It resolves only an observed
call when one unambiguous implementation is already part of the bounded static
flow, preferring constructor/field injection over a method-name fallback.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from orbitkb.analysis.models import AnalysisResult, FlowEdge, Symbol


class BoundedFlowResolver:
    """Links local call expressions to known symbols without inventing edges."""

    def resolve(self, result: AnalysisResult) -> AnalysisResult:
        symbols = {symbol.name: symbol for symbol in result.symbols}
        implementations = set(symbols)
        implementation_types = self._implementation_types(symbols.values())
        injections = {
            edge.source: edge.target
            for edge in result.edges
            if edge.kind == "injects" and "." in edge.source
        }
        result.edges = [
            self._resolve_edge(edge, implementations, injections, implementation_types)
            for edge in result.edges
        ]
        return result

    @staticmethod
    def _implementation_types(symbols: Iterable[Symbol]) -> dict[str, set[str]]:
        implementations: dict[str, set[str]] = {}
        for symbol in symbols:
            for contract in symbol.implements:
                implementations.setdefault(contract, set()).add(symbol.owner)
        return implementations

    @staticmethod
    def _resolve_edge(
        edge: FlowEdge, implementations: set[str], injections: dict[str, str],
        implementation_types: dict[str, set[str]],
    ) -> FlowEdge:
        if edge.kind != "invokes" or edge.target in implementations:
            return edge
        receiver, separator, method = edge.target.rpartition(".")
        owner = edge.source.split(".", 1)[0]
        injected_type = injections.get(f"{owner}.{receiver}") if separator else None
        injected_candidate = f"{injected_type}.{method}" if injected_type else None
        if injected_candidate in implementations:
            return replace(edge, target=injected_candidate, confidence="high")

        implementation_candidates = {
            f"{implementation}.{method}"
            for implementation in implementation_types.get(injected_type or "", set())
            if f"{implementation}.{method}" in implementations
        }
        if len(implementation_candidates) == 1:
            return replace(edge, target=implementation_candidates.pop(), confidence="high")

        candidates = sorted(symbol for symbol in implementations if symbol.endswith(f".{method}"))
        if len(candidates) == 1:
            return replace(edge, target=candidates[0], confidence="medium")
        return edge
