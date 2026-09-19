"""Unit tests for discovery.registry: detector_for (folder-signature matching,
exercised indirectly via test_discovery.py's discover_services tests) and
detector_by_id — the explicit "I already know the stack" lookup used by
`orbitkb index --stack` to bypass matches() for a library/CLI-shaped package that
no heuristic recognizes (see cli.py, orchestrator.index_path)."""
from orbitkb.discovery.go_stack import GoDetector
from orbitkb.discovery.jvm_stack import JvmSpringDetector
from orbitkb.discovery.node_ts import NodeTsDetector
from orbitkb.discovery.python_stack import PythonDetector
from orbitkb.discovery.registry import detector_by_id


def test_detector_by_id_resolves_every_registered_stack():
    assert isinstance(detector_by_id("python"), PythonDetector)
    assert isinstance(detector_by_id("node-ts"), NodeTsDetector)
    assert isinstance(detector_by_id("jvm-spring"), JvmSpringDetector)
    assert isinstance(detector_by_id("go"), GoDetector)


def test_detector_by_id_returns_none_for_an_unknown_stack():
    assert detector_by_id("rust") is None
