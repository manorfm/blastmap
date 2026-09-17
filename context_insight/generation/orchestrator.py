from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from context_insight.db import repository
from context_insight.discovery.base import CodeExcerpt, EndpointHint, ServiceHints, StackDetector
from context_insight.discovery.hashing import file_hash, git_head_commit
from context_insight.discovery.scan_helpers import SKIP_DIRS
from context_insight.discovery.walker import discover_services
from context_insight.generation.backend_base import LLMBackend
from context_insight.generation.llm_harness import generate_with_retry, load_prompt, load_schema

MAX_EXCERPT_CHARS = 20_000


class DiscoveryError(Exception):
    """Raised when index_path cannot find/resolve a service to index."""


@dataclass
class IndexResult:
    service_name: str
    service_id: int
    files_changed: int
    llm_calls: int
    status: str


class ProgressReporter(Protocol):
    """Lets the CLI render progress without generation logic depending on a UI library.

    total_units in service_started is the number of generation "slots" for this
    service (overview + one per endpoint + persistence + messaging, when present) —
    every slot gets exactly one unit_finished call, whether it was actually generated
    or skipped because nothing changed, so a caller can drive an accurate percentage.
    """

    def service_started(self, service: str, total_units: int) -> None: ...
    def unit_started(self, service: str, label: str) -> None: ...
    def unit_finished(self, service: str, label: str, status: str) -> None: ...
    def service_finished(self, service: str) -> None: ...


class NullProgressReporter:
    def service_started(self, service: str, total_units: int) -> None:
        pass

    def unit_started(self, service: str, label: str) -> None:
        pass

    def unit_finished(self, service: str, label: str, status: str) -> None:
        pass

    def service_finished(self, service: str) -> None:
        pass


# ---------------------------------------------------------------------------
# prompt rendering helpers
# ---------------------------------------------------------------------------

def _join_excerpts(excerpts: list[CodeExcerpt], max_chars: int = MAX_EXCERPT_CHARS) -> str:
    parts: list[str] = []
    total = 0
    for e in excerpts:
        block = f"--- {e.file_path} (lines {e.start_line}-{e.end_line}) ---\n{e.text}\n"
        if total + len(block) > max_chars:
            parts.append("... (truncated, excerpt budget reached)")
            break
        parts.append(block)
        total += len(block)
    return "\n".join(parts) if parts else "(no excerpts found)"


def _evidence_from_excerpts(excerpts: list[CodeExcerpt]) -> list[dict]:
    """Turn discovery excerpts into persistable evidence pointers (file + line range).

    This is the evidence an LLM call actually saw when it produced a claim, so it's
    attached as-is to whatever that call generated — it is never fabricated beyond
    what discovery already found.
    """
    return [{"file": e.file_path, "start_line": e.start_line, "end_line": e.end_line} for e in excerpts]


def _folder_tree(root: Path, max_depth: int = 2, max_lines: int = 200) -> str:
    lines: list[str] = []
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        depth = len(current.parts) - root_depth
        if depth >= max_depth:
            dirnames[:] = []
        indent = "  " * depth
        if current != root:
            lines.append(f"{indent}{current.name}/")
        if depth < max_depth:
            for f in sorted(filenames)[:20]:
                lines.append(f"{indent}  {f}")
        if len(lines) >= max_lines:
            break
    return "\n".join(lines[:max_lines]) or "(empty)"


def _render_service_overview_prompt(name: str, stack: str, root: Path, hints: ServiceHints) -> str:
    entry_file = hints.entry_excerpt.file_path if hints.entry_excerpt else "(none found)"
    entry_excerpt = hints.entry_excerpt.text if hints.entry_excerpt else "(no entrypoint file detected)"
    endpoint_paths = "\n".join(f"- {e.method} {e.path}" for e in hints.endpoints) or "(none found)"
    return load_prompt("service_overview").substitute(
        service_name=name,
        stack=stack,
        folder_tree=_folder_tree(root),
        entry_file=entry_file,
        entry_excerpt=entry_excerpt,
        endpoint_paths=endpoint_paths,
    )


def _render_api_detail_prompt(name: str, stack: str, endpoint: EndpointHint, hints: ServiceHints) -> str:
    excerpts = [endpoint.excerpt, *endpoint.extra_excerpts]
    code = _join_excerpts(excerpts)
    outbound = "\n".join(
        f"- [{c.call_kind}] {c.target_hint} ({c.excerpt.file_path}:{c.excerpt.start_line})"
        for c in hints.outbound_calls[:30]
    ) or "(none found)"
    return load_prompt("api_detail").substitute(
        service_name=name,
        stack=stack,
        method=endpoint.method,
        path=endpoint.path,
        code_excerpts=code,
        outbound_call_hints=outbound,
    )


def _render_persistence_prompt(name: str, stack: str, hints: ServiceHints) -> str:
    excerpts = [p.excerpt for p in hints.persistence]
    return load_prompt("persistence").substitute(
        service_name=name, stack=stack, persistence_excerpts=_join_excerpts(excerpts)
    )


def _render_messaging_prompt(name: str, stack: str, hints: ServiceHints) -> str:
    excerpts = [m.excerpt for m in hints.messaging]
    return load_prompt("messaging").substitute(
        service_name=name, stack=stack, messaging_excerpts=_join_excerpts(excerpts)
    )


# ---------------------------------------------------------------------------
# per-service indexing
# ---------------------------------------------------------------------------

def index_service(
    conn: sqlite3.Connection,
    name: str,
    root: Path,
    detector: StackDetector,
    backend: LLMBackend,
    force: bool = False,
    failures_root: Path | None = None,
    progress: ProgressReporter | None = None,
    repository_id: int | None = None,
) -> IndexResult:
    failures_root = failures_root or (Path.home() / ".context_insight" / "failures")
    progress = progress or NullProgressReporter()
    hints = detector.collect_hints(root)
    total_units = 1 + len(hints.endpoints) + (1 if hints.persistence else 0) + (1 if hints.messaging else 0)
    progress.service_started(name, total_units)

    existing = repository.get_service_by_name(conn, name)
    is_new = existing is None
    service_id = repository.ensure_service(conn, name, str(root), detector.id, repository_id=repository_id)

    old_hashes = repository.get_indexed_file_hashes(conn, service_id)
    relevant = hints.relevant_files()
    new_hashes: dict[str, str] = {}
    changed: set[str] = set()
    for rel in relevant:
        path = root / rel
        if not path.is_file():
            continue
        h = file_hash(path)
        new_hashes[rel] = h
        if force or old_hashes.get(rel) != h:
            changed.add(rel)
    removed = set(old_hashes) - set(new_hashes)

    run_id = repository.start_index_run(conn, service_id, backend.name)
    llm_calls = 0
    had_failure = False
    # Files whose derived unit failed to generate this run. Their hash is deliberately
    # NOT persisted to indexed_files below, so next run sees them as "changed" again
    # and retries instead of silently skipping a permanently-broken unit forever.
    failed_files: set[str] = set()

    entry_files = {hints.entry_excerpt.file_path} if hints.entry_excerpt else set()
    needs_overview = force or is_new or bool(changed & entry_files) or not (existing and existing["short_desc"])
    if needs_overview:
        progress.unit_started(name, "overview")
        prompt = _render_service_overview_prompt(name, detector.id, root, hints)
        result = generate_with_retry(
            backend, prompt, load_schema("service_overview"), root, failures_root, f"{name}-overview"
        )
        if result:
            repository.update_service_overview(conn, service_id, result["short_desc"], result["long_desc"])
            llm_calls += 1
            progress.unit_finished(name, "overview", "ok")
        else:
            had_failure = True
            failed_files |= entry_files
            progress.unit_finished(name, "overview", "failed")
    else:
        progress.unit_finished(name, "overview", "skipped")

    keep_keys: set[tuple[str, str]] = set()
    for endpoint in hints.endpoints:
        key = (endpoint.method, endpoint.path)
        keep_keys.add(key)
        existing_api = repository.get_api_by_key(conn, service_id, *key)
        dep_files = endpoint.dependency_files()
        needs_regen = force or existing_api is None or bool(dep_files & changed)
        label = f"{endpoint.method} {endpoint.path}"
        if not needs_regen:
            progress.unit_finished(name, label, "skipped")
            continue
        progress.unit_started(name, label)
        prompt = _render_api_detail_prompt(name, detector.id, endpoint, hints)
        result = generate_with_retry(
            backend, prompt, load_schema("api_detail"), root, failures_root, f"{name}-{endpoint.method}-{endpoint.path}"
        )
        if not result:
            had_failure = True
            failed_files |= dep_files
            progress.unit_finished(name, label, "failed")
            continue
        evidence = _evidence_from_excerpts([endpoint.excerpt, *endpoint.extra_excerpts])
        api_id = repository.upsert_api(
            conn, service_id, endpoint.method, endpoint.path,
            result["summary"], result["description"], result["response_shape"], evidence,
        )
        repository.replace_api_validations(conn, api_id, result["validations"])
        repository.replace_calls_for_api(conn, service_id, api_id, result["calls"], evidence)
        llm_calls += 1
        progress.unit_finished(name, label, "ok")

    repository.prune_apis_not_in(conn, service_id, keep_keys)

    persistence_files = {p.excerpt.file_path for p in hints.persistence}
    if hints.persistence:
        if force or is_new or (changed & persistence_files) or (removed & persistence_files):
            progress.unit_started(name, "persistence")
            prompt = _render_persistence_prompt(name, detector.id, hints)
            result = generate_with_retry(
                backend, prompt, load_schema("persistence"), root, failures_root, f"{name}-persistence"
            )
            if result:
                entities = [
                    {"name": e["name"], "kind": e["kind"], "schema_json": e["fields"]} for e in result["entities"]
                ]
                evidence = _evidence_from_excerpts([p.excerpt for p in hints.persistence])
                repository.replace_persistence_entities(conn, service_id, entities, evidence)
                llm_calls += 1
                progress.unit_finished(name, "persistence", "ok")
            else:
                had_failure = True
                failed_files |= persistence_files
                progress.unit_finished(name, "persistence", "failed")
        else:
            progress.unit_finished(name, "persistence", "skipped")
    else:
        repository.replace_persistence_entities(conn, service_id, [], [])

    messaging_files = {m.excerpt.file_path for m in hints.messaging}
    if hints.messaging:
        if force or is_new or (changed & messaging_files) or (removed & messaging_files):
            progress.unit_started(name, "messaging")
            prompt = _render_messaging_prompt(name, detector.id, hints)
            result = generate_with_retry(
                backend, prompt, load_schema("messaging"), root, failures_root, f"{name}-messaging"
            )
            if result:
                messages = [
                    {
                        "direction": m["direction"],
                        "channel": m["channel"],
                        "shape_json": m["shape"],
                        "description": m["description"],
                    }
                    for m in result["messages"]
                ]
                evidence = _evidence_from_excerpts([m.excerpt for m in hints.messaging])
                repository.replace_messages(conn, service_id, messages, evidence)
                llm_calls += 1
                progress.unit_finished(name, "messaging", "ok")
            else:
                had_failure = True
                failed_files |= messaging_files
                progress.unit_finished(name, "messaging", "failed")
        else:
            progress.unit_finished(name, "messaging", "skipped")
    else:
        repository.replace_messages(conn, service_id, [], [])

    route_files = {e.excerpt.file_path for e in hints.endpoints}
    for rel, h in new_hashes.items():
        if rel in failed_files:
            continue  # retry these next run instead of locking in a broken generation forever
        category = "route" if rel in route_files else (
            "persistence" if rel in persistence_files else ("messaging" if rel in messaging_files else "other")
        )
        repository.set_indexed_file_hash(conn, service_id, rel, h, category)
    if removed:
        repository.remove_indexed_files(conn, service_id, removed)
    repository.commit(conn)

    repository.set_service_last_commit(conn, service_id, git_head_commit(root))
    repository.reconcile_service_call_targets(conn)
    repository.rebuild_search_index_for_service(conn, service_id)

    status = "partial" if had_failure else "ok"
    repository.finish_index_run(
        conn, run_id, status, len(changed) + len(removed), llm_calls,
        "some units failed, see failures dir" if had_failure else None,
    )
    progress.service_finished(name)

    return IndexResult(
        service_name=name, service_id=service_id,
        files_changed=len(changed) + len(removed), llm_calls=llm_calls, status=status,
    )


# ---------------------------------------------------------------------------
# entry point used by the CLI
# ---------------------------------------------------------------------------

def index_path(
    conn: sqlite3.Connection,
    path: Path,
    backend: LLMBackend,
    service_override: str | None = None,
    force: bool = False,
    progress: ProgressReporter | None = None,
    repository_name: str | None = None,
) -> list[IndexResult]:
    candidates = discover_services(path)
    if not candidates:
        raise DiscoveryError(
            f"No supported microservice detected under {path} "
            "(looked for Node/TS, Python, JVM/Spring, and Go boundary markers)."
        )
    if service_override:
        if len(candidates) != 1:
            raise DiscoveryError("--service can only be used when <path> points at a single service")
        candidates = [replace(candidates[0], name=service_override)]

    resolved_path = path.resolve()
    repository_id = repository.ensure_repository(conn, repository_name or resolved_path.name, str(resolved_path))

    return [
        index_service(conn, c.name, c.path, c.detector, backend, force=force, progress=progress, repository_id=repository_id)
        for c in candidates
    ]
