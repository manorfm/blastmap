"""Runs candidate retrieval (blastmap.generation.retrieval.KeywordGraphRetrieval)
against every benchmark task and reports recall — see benchmark/__init__.py for why
this is deliberately recall-only, with no LLM call and no precision claim."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from blastmap.generation.retrieval import KeywordGraphRetrieval

from benchmark.tasks import BenchmarkTask

DEFAULT_MAX_CANDIDATES = 10


@dataclass
class TaskResult:
    task_id: str
    expected: set[str]
    candidates: set[str]

    @property
    def missing(self) -> set[str]:
        return self.expected - self.candidates

    @property
    def recall(self) -> float:
        if not self.expected:
            return 1.0
        return len(self.expected & self.candidates) / len(self.expected)


@dataclass
class BenchmarkReport:
    results: list[TaskResult]

    @property
    def aggregate_recall(self) -> float:
        if not self.results:
            return 1.0
        return sum(r.recall for r in self.results) / len(self.results)


def run_retrieval_recall(
    tasks: list[BenchmarkTask], tmp_dir: Path, max_candidates: int = DEFAULT_MAX_CANDIDATES
) -> BenchmarkReport:
    retrieval = KeywordGraphRetrieval()
    results = []
    for task in tasks:
        conn = task.build_fixture(tmp_dir / f"{task.id}.db")
        candidates = set(retrieval.candidates(conn, task.description, task.hint_services, max_candidates))
        results.append(TaskResult(task_id=task.id, expected=task.expected_services, candidates=candidates))
    return BenchmarkReport(results)
