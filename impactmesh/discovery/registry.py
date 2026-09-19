from __future__ import annotations

from impactmesh.discovery.base import StackDetector
from impactmesh.discovery.go_stack import GoDetector
from impactmesh.discovery.jvm_stack import JvmSpringDetector
from impactmesh.discovery.node_ts import NodeTsDetector
from impactmesh.discovery.python_stack import PythonDetector

# Order matters only in that the first matching detector "claims" a folder.
# Add a new stack by writing one StackDetector implementation and appending it here.
DETECTORS: list[StackDetector] = [
    NodeTsDetector(),
    PythonDetector(),
    JvmSpringDetector(),
    GoDetector(),
]


def detector_for(folder) -> StackDetector | None:
    for detector in DETECTORS:
        if detector.matches(folder):
            return detector
    return None


def detector_by_id(stack_id: str) -> StackDetector | None:
    """Explicit lookup by stack id, bypassing matches() entirely — the escape hatch
    for a folder whose shape no heuristic recognizes (e.g. a library/CLI package
    with its manifest at the repo root and source in a subdirectory) but whose stack
    the caller already knows for certain. See `impactmesh index --stack`."""
    return next((d for d in DETECTORS if d.id == stack_id), None)
