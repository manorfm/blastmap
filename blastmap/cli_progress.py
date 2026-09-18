"""Rich-based terminal progress for `blastmap index`/`update`.

Kept separate from generation/orchestrator.py so the generation logic never depends
on a UI library — it only calls the small ProgressReporter protocol.
"""
from __future__ import annotations

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

_STATUS_STYLE = {
    "ok": "[green]ok[/green]",
    "failed": "[bold red]falhou[/bold red]",
    "skipped": "[dim]sem mudanças[/dim]",
}


class RichProgressReporter:
    """One growing bar per service; finished services stay on screen at 100%, so a
    multi-service run visibly fills up from top to bottom. TimeElapsedColumn keeps
    ticking on the active row even while a single slow LLM call is in flight, so a
    stalled step is visibly still "alive" (elapsed climbing) versus truly hung
    (spinner frozen too, which only happens if the process itself died).
    """

    def __init__(self) -> None:
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.fields[service]}[/bold]"),
            BarColumn(),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TextColumn("{task.fields[detail]}"),
            TimeElapsedColumn(),
        )
        self._tasks: dict[str, int] = {}

    def __enter__(self) -> "RichProgressReporter":
        self._progress.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        self._progress.__exit__(*exc)

    def service_started(self, service: str, total_units: int) -> None:
        task_id = self._progress.add_task(service, total=max(total_units, 1), service=service, detail="iniciando…")
        self._tasks[service] = task_id

    def unit_started(self, service: str, label: str) -> None:
        self._progress.update(self._tasks[service], detail=f"gerando: {label}…")

    def unit_finished(self, service: str, label: str, status: str) -> None:
        style = _STATUS_STYLE.get(status, status)
        self._progress.update(self._tasks[service], advance=1, detail=f"{label}: {style}")

    def service_finished(self, service: str) -> None:
        self._progress.update(self._tasks[service], detail="[bold green]concluído[/bold green]")
