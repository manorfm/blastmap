"""Unit tests for prompt composition in generation/orchestrator.py — kept separate from
test_orchestrator.py (which drives the full indexing pipeline) because these assert on
the exact text handed to the LLM, not on what ends up persisted."""
from impactmesh.discovery.base import (
    CodeExcerpt,
    EndpointHint,
    OutboundCallHint,
    ServiceHints,
)
from impactmesh.generation.orchestrator import _render_api_detail_prompt


def _excerpt(file_path: str) -> CodeExcerpt:
    return CodeExcerpt(file_path=file_path, start_line=1, end_line=5, text="...")


def test_api_detail_prompt_only_includes_outbound_calls_from_the_endpoints_own_files():
    endpoint_a = EndpointHint(
        method="POST", path="/orders", component_hint="main", excerpt=_excerpt("orders.py"),
    )
    endpoint_b = EndpointHint(
        method="GET", path="/health", component_hint="main", excerpt=_excerpt("health.py"),
    )
    hints = ServiceHints(
        endpoints=[endpoint_a, endpoint_b],
        outbound_calls=[
            OutboundCallHint(call_kind="http", target_hint="payments-service", excerpt=_excerpt("orders.py")),
            OutboundCallHint(call_kind="http", target_hint="unrelated-service", excerpt=_excerpt("health.py")),
        ],
    )

    prompt = _render_api_detail_prompt("orders-service", "python", endpoint_a, hints)

    assert "payments-service" in prompt
    assert "unrelated-service" not in prompt


def test_api_detail_prompt_includes_calls_found_in_extra_excerpts_too():
    endpoint = EndpointHint(
        method="POST", path="/orders", component_hint="main", excerpt=_excerpt("orders.py"),
        extra_excerpts=[_excerpt("helpers.py")],
    )
    hints = ServiceHints(
        endpoints=[endpoint],
        outbound_calls=[
            OutboundCallHint(call_kind="http", target_hint="payments-service", excerpt=_excerpt("helpers.py")),
        ],
    )

    prompt = _render_api_detail_prompt("orders-service", "python", endpoint, hints)

    assert "payments-service" in prompt
