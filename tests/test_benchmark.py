"""CI-safe regression guard: does candidate retrieval (no LLM involved) still surface
every benchmark task's expected services? See benchmark/__init__.py and this
project's README for why this measures recall only, not end-to-end precision.
"""
from pathlib import Path

from benchmark.runner import run_retrieval_recall
from benchmark.tasks import TASKS


def test_every_benchmark_task_is_fully_recalled(tmp_path: Path):
    report = run_retrieval_recall(TASKS, tmp_path)

    failing = [r for r in report.results if r.missing]
    assert not failing, f"retrieval regression: {[(r.task_id, r.missing) for r in failing]}"
    assert report.aggregate_recall == 1.0


def test_report_has_one_result_per_task(tmp_path: Path):
    report = run_retrieval_recall(TASKS, tmp_path)

    assert {r.task_id for r in report.results} == {t.id for t in TASKS}
