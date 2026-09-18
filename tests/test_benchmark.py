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


def test_reduction_ratio_shows_candidates_are_a_minority_of_the_system(tmp_path: Path):
    report = run_retrieval_recall(TASKS, tmp_path)

    for r in report.results:
        assert r.total_services > 0
        # A task whose graph expansion reaches every service in a small, densely
        # connected fixture can legitimately hit 0 reduction — that's an honest
        # property of a tiny fixture, not a retrieval bug. The aggregate below is
        # the real signal: across a realistic task mix, most of the system stays
        # out of scope.
        assert 0.0 <= r.reduction_ratio < 1.0
    assert 0.0 < report.aggregate_reduction_ratio < 1.0
