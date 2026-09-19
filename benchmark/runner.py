"""Runs candidate retrieval (impactmesh.generation.retrieval.KeywordGraphRetrieval)
against every benchmark task and reports recall — see benchmark/__init__.py for why
this is deliberately recall-only, with no LLM call and no precision claim."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from impactmesh.db.repositories import services as services_repo
from impactmesh.generation.retrieval import CandidateRetrieval, KeywordGraphRetrieval

from benchmark.tasks import BenchmarkTask

DEFAULT_MAX_CANDIDATES = 10


@dataclass
class TaskResult:
    task_id: str
    expected: set[str]
    candidates: set[str]
    total_services: int

    @property
    def missing(self) -> set[str]:
        return self.expected - self.candidates

    @property
    def recall(self) -> float:
        if not self.expected:
            return 1.0
        return len(self.expected & self.candidates) / len(self.expected)

    @property
    def reduction_ratio(self) -> float:
        """Of every indexed service in this task's fixture, the fraction retrieval
        did NOT have to surface as a candidate — the deterministic, non-circular
        half of "exploration reduction" (see benchmark/__init__.py): a structural
        fact about the candidate set size, not a simulated agent's behavior.
        """
        if not self.total_services:
            return 0.0
        return 1 - len(self.candidates) / self.total_services


@dataclass
class BenchmarkReport:
    results: list[TaskResult]

    @property
    def aggregate_recall(self) -> float:
        if not self.results:
            return 1.0
        return sum(r.recall for r in self.results) / len(self.results)

    @property
    def aggregate_reduction_ratio(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.reduction_ratio for r in self.results) / len(self.results)


def run_retrieval_recall(
    tasks: list[BenchmarkTask],
    tmp_dir: Path,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    retrieval: CandidateRetrieval | None = None,
) -> BenchmarkReport:
    retrieval = retrieval or KeywordGraphRetrieval()
    results = []
    for task in tasks:
        conn = task.build_fixture(tmp_dir / f"{task.id}.db")
        candidates = set(retrieval.candidates(conn, task.description, task.hint_services, max_candidates))
        total_services = len(services_repo.list_services(conn))
        results.append(
            TaskResult(task_id=task.id, expected=task.expected_services, candidates=candidates, total_services=total_services)
        )
    return BenchmarkReport(results)
