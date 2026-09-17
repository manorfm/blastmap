from pathlib import Path

from blastmap.discovery.jvm_stack import JvmSpringDetector
from blastmap.discovery.node_ts import NodeTsDetector
from blastmap.discovery.python_stack import PythonDetector
from blastmap.discovery.walker import discover_services

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
