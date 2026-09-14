from __future__ import annotations

from context_insight.discovery.base import StackDetector
from context_insight.discovery.go_stack import GoDetector
from context_insight.discovery.jvm_stack import JvmSpringDetector
from context_insight.discovery.node_ts import NodeTsDetector
from context_insight.discovery.python_stack import PythonDetector

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
