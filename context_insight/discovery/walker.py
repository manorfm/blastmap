from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from context_insight.discovery.base import StackDetector
from context_insight.discovery.registry import detector_for
from context_insight.discovery.scan_helpers import SKIP_DIRS


@dataclass
class ServiceCandidate:
    name: str
    path: Path
    detector: StackDetector


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return slug or "service"


def discover_services(root: Path) -> list[ServiceCandidate]:
    """Find every microservice under root.

    If root itself is a service (single-repo mode), returns just that one.
    Otherwise walks top-down and treats the first matching folder on each branch
    as a service boundary, without descending further into it (so nested vendored
    code never gets mistaken for a second service).
    """
    root = root.resolve()
    detector = detector_for(root)
    if detector is not None:
        return [ServiceCandidate(name=slugify(root.name), path=root, detector=detector)]

    candidates: list[ServiceCandidate] = []
    for dirpath, dirnames, _filenames in os.walk(root):
        current = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        if current == root:
            continue
        found = detector_for(current)
        if found is not None:
            candidates.append(ServiceCandidate(name=slugify(current.name), path=current, detector=found))
            dirnames[:] = []

    return _dedupe_names(candidates)


def _dedupe_names(candidates: list[ServiceCandidate]) -> list[ServiceCandidate]:
    seen: dict[str, int] = {}
    for c in candidates:
        seen[c.name] = seen.get(c.name, 0) + 1
    if all(count == 1 for count in seen.values()):
        return candidates
    result: list[ServiceCandidate] = []
    used: set[str] = set()
    for c in candidates:
        name = c.name
        if seen[c.name] > 1:
            name = f"{c.path.parent.name}-{c.name}".strip("-").lower()
            name = slugify(name)
        base = name
        i = 2
        while name in used:
            name = f"{base}-{i}"
            i += 1
        used.add(name)
        result.append(ServiceCandidate(name=name, path=c.path, detector=c.detector))
    return result
