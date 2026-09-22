"""Reproducible static-analysis scale measurements for a local machine baseline."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import median
import tempfile
import time
import tracemalloc

from orbitkb.analysis.engine import StaticAnalysisEngine


@dataclass(frozen=True)
class ScaleMeasurement:
    files: int
    edges: int
    runs: int
    median_elapsed_ms: float
    max_peak_memory_bytes: int


@dataclass(frozen=True)
class ScaleProfile:
    repetitions: int
    measurements: tuple[ScaleMeasurement, ...]

    def as_dict(self) -> dict[str, int | list[dict[str, int | float]]]:
        return {
            "repetitions": self.repetitions,
            "measurements": [
                {
                    "files": item.files,
                    "edges": item.edges,
                    "runs": item.runs,
                    "median_elapsed_ms": item.median_elapsed_ms,
                    "max_peak_memory_bytes": item.max_peak_memory_bytes,
                }
                for item in self.measurements
            ],
        }


def run_scale_profile(file_counts: tuple[int, ...], repetitions: int = 3) -> ScaleProfile:
    """Measure generated Go-handler corpora sequentially and summarize each size.

    The result is intentionally a local baseline, not a portable latency SLO: parser
    cost varies with CPU, Python version and Tree-sitter binaries.
    """
    if repetitions < 1 or any(count < 1 for count in file_counts):
        raise ValueError("file counts and repetitions must be positive")
    measurements = tuple(_measure_count(count, repetitions) for count in file_counts)
    return ScaleProfile(repetitions, measurements)


def _measure_count(files: int, repetitions: int) -> ScaleMeasurement:
    samples = tuple(_run_once(files) for _ in range(repetitions))
    edge_counts = {sample.edges for sample in samples}
    if len(edge_counts) != 1:
        raise RuntimeError("generated corpus produced inconsistent static-analysis edge counts")
    return ScaleMeasurement(
        files=files,
        edges=edge_counts.pop(),
        runs=repetitions,
        median_elapsed_ms=round(median(sample.elapsed_ms for sample in samples), 2),
        max_peak_memory_bytes=max(sample.peak_memory_bytes for sample in samples),
    )


@dataclass(frozen=True)
class _ScaleSample:
    edges: int
    elapsed_ms: float
    peak_memory_bytes: int


def _run_once(files: int) -> _ScaleSample:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for index in range(files):
            (root / f"handler_{index}.go").write_text(
                f"package main\nfunc handle{index}() {{ service.Process({index}) }}\n", encoding="utf-8",
            )
        tracemalloc.start()
        started = time.perf_counter()
        result = StaticAnalysisEngine().analyze(root, "go")
        elapsed_ms = (time.perf_counter() - started) * 1000
        _, peak_memory_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    return _ScaleSample(len(result.edges), elapsed_ms, peak_memory_bytes)
