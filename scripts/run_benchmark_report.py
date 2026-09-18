#!/usr/bin/env python3
"""Prints the retrieval-recall benchmark as a human-readable table. The same checks
run in CI as tests/test_benchmark.py; this is for an ad hoc look at the numbers.

    python scripts/run_benchmark_report.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from benchmark.runner import run_retrieval_recall
from benchmark.tasks import TASKS


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        report = run_retrieval_recall(TASKS, Path(tmp))

    width = max(len(r.task_id) for r in report.results)
    for r in report.results:
        status = "ok" if not r.missing else f"MISSING {sorted(r.missing)}"
        print(f"{r.task_id:<{width}}  recall={r.recall:.2f}  reduction={r.reduction_ratio:.2f}  {status}")
    print(f"\naggregate recall: {report.aggregate_recall:.2f} over {len(report.results)} tasks")
    print(f"aggregate reduction: {report.aggregate_reduction_ratio:.2f} (fraction of the system NOT surfaced as a candidate)")
    return 0 if report.aggregate_recall == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
