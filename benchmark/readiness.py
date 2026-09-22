"""Compose deterministic evaluation evidence into an honest readiness summary."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from benchmark.runner import run_retrieval_recall
from benchmark.static_evaluation import CASES, run_static_evaluation
from benchmark.tasks import TASKS


CONDITIONS = (
    "run the opt-in container E2E on the target Docker/CI environment",
    "verify change-surface precision and recall against real repository changes using Git",
    "operate SQLite on one host/process domain or provide distributed coordination",
    "profile representative repositories before introducing an AST cache or parallel indexing",
)


@dataclass(frozen=True)
class ReadinessAudit:
    static_facts_passed: bool
    change_surface_candidates_passed: bool
    conditions: tuple[str, ...]

    @property
    def status(self) -> str:
        """Never report unconditional production approval from synthetic evidence."""
        return "conditional" if self.static_facts_passed and self.change_surface_candidates_passed else "blocked"

    def as_dict(self) -> dict[str, bool | str | list[str]]:
        return {
            "status": self.status,
            "static_facts_passed": self.static_facts_passed,
            "change_surface_candidates_passed": self.change_surface_candidates_passed,
            "conditions": list(self.conditions),
        }


def run_readiness_audit(work_dir: Path) -> ReadinessAudit:
    """Run cheap, local evidence checks and preserve remaining operator conditions."""
    work_dir.mkdir(parents=True, exist_ok=True)
    static = run_static_evaluation(CASES, work_dir / "static")
    candidates = run_retrieval_recall(TASKS, work_dir / "change-surface")
    return ReadinessAudit(
        static_facts_passed=static.aggregate_precision == 1.0 and static.aggregate_recall == 1.0,
        change_surface_candidates_passed=(
            candidates.aggregate_recall == 1.0
            and candidates.aggregate_candidate_precision == 1.0
            and candidates.aggregate_candidate_recall == 1.0
        ),
        conditions=CONDITIONS,
    )
