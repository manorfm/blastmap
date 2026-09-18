from pathlib import Path

from context_insight.discovery.jvm_stack import JvmSpringDetector
from context_insight.discovery.node_ts import NodeTsDetector
from context_insight.discovery.python_stack import PythonDetector
from context_insight.discovery.walker import discover_services

SAMPLE_ROOT = Path(__file__).resolve().parent.parent / "verify" / "sample_project"


def test_discover_services_finds_all_three():
    candidates = discover_services(SAMPLE_ROOT)
    names = sorted(c.name for c in candidates)
    assert names == ["inventory-service", "orders-service", "payments-service"]


def test_python_detector_matches_and_finds_hints():
    folder = SAMPLE_ROOT / "orders-service"
    detector = PythonDetector()
    assert detector.matches(folder)

    hints = detector.collect_hints(folder)
    assert hints.entry_excerpt is not None
    assert hints.entry_excerpt.file_path == "main.py"

    assert len(hints.endpoints) == 1
    endpoint = hints.endpoints[0]
    assert endpoint.method == "POST"
    assert endpoint.path == "/orders"

    outbound_kinds = {c.call_kind for c in hints.outbound_calls}
    assert "http" in outbound_kinds

    assert any(m.direction == "publishes" for m in hints.messaging)  # kafka producer.send
    assert any(p.name_hint == "Order" for p in hints.persistence)


def test_python_endpoint_falls_back_to_file_stem_with_no_enclosing_class():
    folder = SAMPLE_ROOT / "orders-service"
    hints = PythonDetector().collect_hints(folder)
    endpoint = hints.endpoints[0]
    assert endpoint.component_hint == "main"  # function-based routing, no class wraps it
    assert endpoint.extra_excerpts == []  # every call in the handler is a library call


COMPONENT_FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "component_python"


def test_python_endpoint_inside_a_class_uses_the_class_as_its_component():
    hints = PythonDetector().collect_hints(COMPONENT_FIXTURE_ROOT)
    endpoint = next(e for e in hints.endpoints if e.path == "/orders")
    assert endpoint.component_hint == "OrdersController"


def test_python_endpoint_resolves_a_locally_defined_helper_into_extra_excerpts():
    hints = PythonDetector().collect_hints(COMPONENT_FIXTURE_ROOT)
    endpoint = next(e for e in hints.endpoints if e.path == "/orders")
    assert len(endpoint.extra_excerpts) == 1
    assert endpoint.extra_excerpts[0].file_path == "routes/orders.py"
    assert "def format_total" in endpoint.extra_excerpts[0].text


def test_node_ts_detector_matches_and_finds_hints():
    folder = SAMPLE_ROOT / "payments-service"
    detector = NodeTsDetector()
    assert detector.matches(folder)

    hints = detector.collect_hints(folder)
    assert len(hints.endpoints) == 1
    assert hints.endpoints[0].method == "POST"
    assert hints.endpoints[0].path == "/charge"

    assert any(m.channel_hint for m in hints.messaging)
    assert any(p.name_hint == "transactions" for p in hints.persistence)


def test_jvm_spring_detector_matches_and_finds_hints():
    folder = SAMPLE_ROOT / "inventory-service"
    detector = JvmSpringDetector()
    assert detector.matches(folder)

    hints = detector.collect_hints(folder)
    assert hints.entry_excerpt is not None

    assert len(hints.endpoints) == 1
    assert hints.endpoints[0].method == "GET"
    assert hints.endpoints[0].path == "/stock/{sku}"

    assert not hints.outbound_calls  # leaf service, no outbound calls
    assert any(p.name_hint == "Stock" for p in hints.persistence)
