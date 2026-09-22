#!/usr/bin/env python3
"""Prints the deterministic change-surface candidate evaluation.

The same checks run in CI as tests/test_benchmark.py; this is for an ad hoc look at
the reviewed candidate envelope, not an LLM-quality claim.

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
        status = "ok" if not r.missing and not r.unexpected_candidates else (
            f"MISSING {sorted(r.missing)} unexpected={sorted(r.unexpected_candidates)}"
        )
        print(
            f"{r.task_id:<{width}}  impact_recall={r.recall:.2f} "
            f"candidate_precision={r.candidate_precision:.2f} "
            f"candidate_recall={r.candidate_recall:.2f} reduction={r.reduction_ratio:.2f}  {status}"
        )
    print(f"\naggregate impact recall: {report.aggregate_recall:.2f} over {len(report.results)} tasks")
    print(f"aggregate candidate precision: {report.aggregate_candidate_precision:.2f}")
    print(f"aggregate candidate recall: {report.aggregate_candidate_recall:.2f}")
    print(f"aggregate reduction: {report.aggregate_reduction_ratio:.2f} (fraction of the system NOT surfaced as a candidate)")
    return 0 if report.aggregate_recall == report.aggregate_candidate_precision == report.aggregate_candidate_recall == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
