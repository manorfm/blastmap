"""Evaluate deterministic change-surface candidate retrieval against golden cases.

This evaluates the bounded SQL retrieval stage only. LLM synthesis remains a
separate, real-world feedback/Git-verification concern.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orbitkb.db.repositories import services as services_repo
from orbitkb.generation.retrieval import CandidateRetrieval, KeywordGraphRetrieval

from benchmark.tasks import BenchmarkTask

DEFAULT_MAX_CANDIDATES = 10


@dataclass
class TaskResult:
    task_id: str
    expected_impacted: set[str]
    expected_candidates: set[str]
    candidates: set[str]
    total_services: int

    @property
    def missing(self) -> set[str]:
        return self.expected_impacted - self.candidates

    @property
    def unexpected_candidates(self) -> set[str]:
        return self.candidates - self.expected_candidates

    @property
    def recall(self) -> float:
        if not self.expected_impacted:
            return 1.0
        return len(self.expected_impacted & self.candidates) / len(self.expected_impacted)

    @property
    def candidate_recall(self) -> float:
        if not self.expected_candidates:
            return 1.0
        return len(self.expected_candidates & self.candidates) / len(self.expected_candidates)

    @property
    def candidate_precision(self) -> float:
        if not self.candidates:
            return 1.0
        return len(self.expected_candidates & self.candidates) / len(self.candidates)

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

    @property
    def aggregate_candidate_recall(self) -> float:
        return _aggregate_ratio(self.results, "expected_candidates", "candidates")

    @property
    def aggregate_candidate_precision(self) -> float:
        return _aggregate_ratio(self.results, "candidates", "expected_candidates")


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
            TaskResult(
                task_id=task.id,
                expected_impacted=task.expected_impacted_services,
                expected_candidates=task.expected_candidates,
                candidates=candidates,
                total_services=total_services,
            )
        )
    return BenchmarkReport(results)


def _aggregate_ratio(results: list[TaskResult], denominator_attribute: str, comparison_attribute: str) -> float:
    """Aggregate by case identity so matching names in separate fixtures do not mask failures."""
    denominator = {
        (result.task_id, service)
        for result in results
        for service in getattr(result, denominator_attribute)
    }
    comparison = {
        (result.task_id, service)
        for result in results
        for service in getattr(result, comparison_attribute)
    }
    return 1.0 if not denominator else len(denominator & comparison) / len(denominator)
