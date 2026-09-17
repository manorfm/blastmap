from __future__ import annotations

import argparse
import sys
from pathlib import Path

from blastmap.cli_progress import RichProgressReporter
from blastmap.config import resolve_backend
from blastmap.db import repository
from blastmap.db.connection import DEFAULT_DB_PATH, open_db
from blastmap.export.markdown import export_markdown
from blastmap.generation.backend_base import GenerationError
from blastmap.generation.orchestrator import DiscoveryError, index_path, index_service


def _cmd_index(args: argparse.Namespace) -> int:
    conn = open_db(args.db)
    backend = resolve_backend(args.backend, args.model, args.claude_bare, args.codex_api_key)
    try:
        with RichProgressReporter() as progress:
            results = index_path(
                conn, Path(args.path), backend, service_override=args.service, force=args.force,
                progress=progress, repository_name=args.repository_name,
            )
    except (DiscoveryError, GenerationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for r in results:
        print(f"{r.service_name}: status={r.status} files_changed={r.files_changed} llm_calls={r.llm_calls}")
    return 0 if all(r.status == "ok" for r in results) else 1


def _cmd_update(args: argparse.Namespace) -> int:
    conn = open_db(args.db)
    row = repository.get_service_by_name(conn, args.service)
    if row is None:
        print(f"error: unknown service {args.service!r} (run `blastmap list`)", file=sys.stderr)
        return 1
    root = Path(row["root_path"])
    if not root.is_dir():
        print(
            f"error: root path for {args.service!r} no longer exists: {root}\n"
            f"       re-run `blastmap index <newpath> --service {args.service}` instead.",
            file=sys.stderr,
        )
        return 1
    from blastmap.discovery.registry import detector_for

    detector = detector_for(root)
    if detector is None:
        print(f"error: {root} no longer matches any known stack", file=sys.stderr)
        return 1
    backend = resolve_backend(args.backend, args.model, args.claude_bare, args.codex_api_key)
    with RichProgressReporter() as progress:
        result = index_service(conn, args.service, root, detector, backend, force=args.force, progress=progress)
    print(f"{result.service_name}: status={result.status} files_changed={result.files_changed} llm_calls={result.llm_calls}")
    return 0 if result.status == "ok" else 1


def _cmd_list(args: argparse.Namespace) -> int:
    conn = open_db(args.db)
    rows = repository.list_services(conn)
    if not rows:
        print("(nenhum serviço indexado ainda)")
        return 0
    for r in rows:
        print(f"{r['name']:<30} stack={r['stack'] or '?':<14} apis={r['api_count']:<3} {r['short_desc'] or ''}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    conn = open_db(args.db)
    if args.service:
        row = repository.get_service_by_name(conn, args.service)
        if row is None:
            print(f"error: unknown service {args.service!r}", file=sys.stderr)
            return 1
        hashes = repository.get_indexed_file_hashes(conn, row["id"])
        print(f"service: {row['name']} ({row['stack']}) — {row['root_path']}")
        print(f"last_commit: {row['last_commit']}")
        print(f"indexed files: {len(hashes)}")
        for run in repository.recent_index_runs(conn, row["id"], limit=5):
            print(
                f"  run#{run['id']} {run['started_at']} status={run['status']} backend={run['backend']} "
                f"files_changed={run['files_changed']} llm_calls={run['llm_calls']} notes={run['notes']}"
            )
    else:
        services = repository.list_services(conn)
        print(f"services indexed: {len(services)}")
        for run in repository.recent_index_runs(conn, limit=10):
            svc = repository.get_service_by_id(conn, run["service_id"]) if run["service_id"] else None
            name = svc["name"] if svc else "?"
            print(
                f"  run#{run['id']} service={name} status={run['status']} backend={run['backend']} "
                f"files_changed={run['files_changed']} llm_calls={run['llm_calls']}"
            )
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    conn = open_db(args.db)
    written = export_markdown(conn, Path(args.out), service_filter=args.service)
    print(f"wrote {len(written)} files under {args.out}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from blastmap.mcp.server import build_server

    backend = resolve_backend(args.backend, args.model, args.claude_bare, args.codex_api_key)
    server = build_server(args.db, backend=backend)
    server.run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="blastmap")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_backend_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--backend", choices=["claude", "codex"], default=None)
        p.add_argument("--model", default=None)
        p.add_argument("--claude-bare", action="store_true", help="Use ANTHROPIC_API_KEY billing instead of the Claude Code subscription session")
        p.add_argument("--codex-api-key", action="store_true", help="Use CODEX_API_KEY billing instead of the ChatGPT subscription session")
        p.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)

    p_index = sub.add_parser("index", help="Index a monorepo root or a single service repo")
    p_index.add_argument("path")
    p_index.add_argument("--service", default=None, help="Override the inferred service name (only valid for a single-service path)")
    p_index.add_argument("--repository-name", default=None, help="Explicit repository name; avoids collisions when indexing several repos into one shared DB")
    p_index.add_argument("--force", action="store_true", help="Regenerate everything, ignoring file-hash skip")
    add_backend_args(p_index)
    p_index.set_defaults(func=_cmd_index)

    p_update = sub.add_parser("update", help="Re-index one already-known service by name")
    p_update.add_argument("service")
    p_update.add_argument("--force", action="store_true")
    add_backend_args(p_update)
    p_update.set_defaults(func=_cmd_update)

    p_list = sub.add_parser("list", help="List indexed services")
    p_list.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    p_list.set_defaults(func=_cmd_list)

    p_status = sub.add_parser("status", help="Show indexing status/history")
    p_status.add_argument("service", nargs="?", default=None)
    p_status.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    p_status.set_defaults(func=_cmd_status)

    p_export = sub.add_parser("export", help="Export the database to Markdown")
    p_export.add_argument("format", choices=["md"])
    p_export.add_argument("--out", default="docs")
    p_export.add_argument("--service", default=None)
    p_export.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    p_export.set_defaults(func=_cmd_export)

    p_serve = sub.add_parser("serve", help="Run the MCP server (stdio)")
    p_serve.add_argument("--transport", choices=["stdio"], default="stdio")
    add_backend_args(p_serve)  # find_change_surface is the only tool that uses a backend
    p_serve.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ncancelado pelo usuário (Ctrl+C)", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
