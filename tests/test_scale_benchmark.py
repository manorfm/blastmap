"""Regression coverage for the reproducible static-analysis scale profile."""
from benchmark.scale import run_scale_profile


def test_scale_profile_reports_one_structured_baseline_per_requested_size():
    report = run_scale_profile((5, 10), repetitions=1)

    assert [(item.files, item.edges, item.runs) for item in report.measurements] == [(5, 5, 1), (10, 10, 1)]
    assert all(item.median_elapsed_ms >= 0 for item in report.measurements)
    assert all(item.max_peak_memory_bytes > 0 for item in report.measurements)
    assert report.as_dict()["repetitions"] == 1


def test_scale_profile_rejects_non_positive_inputs():
    try:
        run_scale_profile((0,), repetitions=1)
    except ValueError as error:
        assert "positive" in str(error)
    else:
        raise AssertionError("non-positive file count must fail")
