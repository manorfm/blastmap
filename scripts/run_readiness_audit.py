#!/usr/bin/env python3
"""Print OrbitKB's deterministic readiness evidence and remaining conditions."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from benchmark.readiness import run_readiness_audit


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        audit = run_readiness_audit(Path(directory))
    print(json.dumps(audit.as_dict(), sort_keys=True))
    return 0 if audit.status == "conditional" else 1


if __name__ == "__main__":
    raise SystemExit(main())
