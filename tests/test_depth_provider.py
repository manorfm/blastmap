from pathlib import Path

from orbitkb.analysis.depth import DepthMode, NoopDepthProvider, _parse_edges, resolve_depth_provider
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
            return [FlowEdge("create", "orders.UseCase.Execute", "invokes", Evidence("usecase.go", 4, 4), origin="codegraph")]

    result = StaticAnalysisEngine(depth_provider=Provider()).analyze(tmp_path, "go")

    assert result.entrypoints[0].symbol == "create"
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
