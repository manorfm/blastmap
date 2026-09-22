from pathlib import Path

from orbitkb.analysis.depth import (
    DepthMode,
    McpDepthProvider,
    NoopDepthProvider,
    _parse_edges,
    resolve_depth_provider,
)
from orbitkb.analysis.engine import StaticAnalysisEngine
from orbitkb.analysis.models import AnalysisResult, Evidence, FlowEdge


def test_off_mode_always_uses_a_noop_provider():
    provider = resolve_depth_provider(DepthMode.OFF, command=None, args=(), tool_name="trace_entrypoint")

    assert isinstance(provider, NoopDepthProvider)
    assert provider.enrich(Path("."), AnalysisResult()) == []


def test_augment_mode_without_a_command_degrades_to_noop():
    provider = resolve_depth_provider(DepthMode.AUGMENT, command=None, args=(), tool_name="trace_entrypoint")

    assert isinstance(provider, NoopDepthProvider)


def test_require_mode_rejects_missing_mcp_command():
    try:
        resolve_depth_provider(DepthMode.REQUIRE, command=None, args=(), tool_name="trace_entrypoint")
    except ValueError as error:
        assert "requires --depth-command" in str(error)
    else:
        raise AssertionError("require mode must reject a missing provider command")


def test_engine_keeps_static_analysis_when_a_provider_adds_depth(tmp_path: Path):
    (tmp_path / "main.go").write_text(
        'package main\nfunc create() {}\nfunc main() { router.POST("/orders", create) }\n', encoding="utf-8"
    )

    class Provider:
        def enrich(self, root: Path, analysis: AnalysisResult) -> list[FlowEdge]:
            return [FlowEdge("main.create", "orders.UseCase.Execute", "invokes", Evidence("usecase.go", 4, 4), origin="codegraph")]

    result = StaticAnalysisEngine(depth_provider=Provider()).analyze(tmp_path, "go")

    assert result.entrypoints[0].symbol == "main.create"
    assert result.edges[-1].origin == "codegraph"


def test_depth_payload_drops_malformed_edges_without_discarding_valid_ones():
    edges = _parse_edges(
        {
            "edges": [
                {"to": "UseCase.execute", "kind": "invokes", "evidence": {"file": "usecase.go", "start_line": 4, "end_line": 4}},
                {"kind": "invokes"},
                {"to": "bad", "kind": "not-a-flow-kind"},
            ]
        },
        "Handler.create",
    )

    assert [(edge.source, edge.target, edge.origin) for edge in edges] == [
        ("Handler.create", "UseCase.execute", "codegraph")
    ]


def test_depth_payload_is_capped_to_its_declared_edge_budget():
    edges = _parse_edges(
        {"edges": [{"to": "UseCase.one", "kind": "invokes"}, {"to": "UseCase.two", "kind": "invokes"}]},
        "Handler.create",
        max_edges=1,
    )

    assert [edge.target for edge in edges] == ["UseCase.one"]


def test_depth_provider_rejects_an_invalid_execution_budget():
    try:
        resolve_depth_provider(DepthMode.AUGMENT, command="codegraph", args=(), tool_name="trace", max_edges=0)
    except ValueError as error:
        assert "--depth-max-edges" in str(error)
    else:
        raise AssertionError("invalid depth budget must be rejected")


def test_augment_provider_caches_a_bounded_enrichment_without_recording_payload(tmp_path, monkeypatch):
    provider = McpDepthProvider("depth", (), "trace", DepthMode.AUGMENT)
    analysis = AnalysisResult(entrypoints=[])
    calls = 0

    async def enrich(root, value):
        nonlocal calls
        calls += 1
        return [FlowEdge("a", "b", "invokes", Evidence("a.go", 1, 1), origin="codegraph")]

    monkeypatch.setattr(provider, "_enrich", enrich)

    assert provider.enrich(tmp_path, analysis)
    assert provider.enrich(tmp_path, analysis)
    assert calls == 1
    assert provider.metrics()["cache_hits"] == 1
    assert "a.go" not in str(provider.metrics())


def test_augment_provider_opens_its_circuit_after_repeated_failures(tmp_path, monkeypatch):
    provider = McpDepthProvider("depth", (), "trace", DepthMode.AUGMENT, circuit_failure_threshold=2)

    async def failing(root, analysis):
        raise RuntimeError("provider payload must not be logged")

    monkeypatch.setattr(provider, "_enrich", failing)
    analysis = AnalysisResult(entrypoints=[])

    assert provider.enrich(tmp_path, analysis) == []
    assert provider.enrich(tmp_path, analysis) == []
    assert provider.enrich(tmp_path, analysis) == []
    assert provider.metrics()["circuit_open"] is True
    assert provider.metrics()["failures"] == 2
