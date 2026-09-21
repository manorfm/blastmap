from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import orbitkb
from orbitkb.analysis.depth import DepthMode, resolve_depth_provider
from orbitkb.cli_progress import RichProgressReporter
from orbitkb.config import resolve_backend
from orbitkb.db.connection import DEFAULT_DB_PATH, open_db
from orbitkb.db.repositories import index_runs as index_runs_repo
from orbitkb.db.repositories import indexed_files as indexed_files_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.db.repositories import verification as verification_repo
from orbitkb.export.markdown import export_markdown
from orbitkb.export.mermaid import export_mermaid
from orbitkb.generation import change_surface
from orbitkb.generation.backend_base import GenerationError
from orbitkb.generation.embeddings import try_create_default_backend
from orbitkb.generation.orchestrator import DiscoveryError, index_path, index_service
from orbitkb.generation.verification import verify_change_surface


def _cmd_index(args: argparse.Namespace) -> int:
    conn = open_db(args.db)
    backend = resolve_backend(args.backend, args.model, args.claude_bare, args.codex_api_key)
    embedding_backend = try_create_default_backend()
    try:
        depth_provider = resolve_depth_provider(
            DepthMode(args.depth_mode), args.depth_command, tuple(args.depth_arg), args.depth_tool,
            args.depth_timeout, args.depth_max_edges,
        )
        with RichProgressReporter() as progress:
            results = index_path(
                conn, Path(args.path), backend, service_override=args.service, force=args.force,
                progress=progress, repository_name=args.repository_name, embedding_backend=embedding_backend,
                stack_override=args.stack, depth_provider=depth_provider,
            )
    except (DiscoveryError, GenerationError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for r in results:
        print(
            f"{r.service_name}: status={r.status} files_changed={r.files_changed} llm_calls={r.llm_calls} "
            f"cost_usd={r.cost_usd}"
        )
    return 0 if all(r.status == "ok" for r in results) else 1


def _cmd_update(args: argparse.Namespace) -> int:
    conn = open_db(args.db)
    row = services_repo.get_service_by_name(conn, args.service)
    if row is None:
        print(f"error: unknown service {args.service!r} (run `orbitkb list`)", file=sys.stderr)
        return 1
    root = Path(row["root_path"])
    if not root.is_dir():
        print(
            f"error: root path for {args.service!r} no longer exists: {root}\n"
            f"       re-run `orbitkb index <newpath> --service {args.service}` instead.",
            file=sys.stderr,
        )
        return 1
    from orbitkb.discovery.registry import detector_for

    detector = detector_for(root)
    if detector is None:
        print(f"error: {root} no longer matches any known stack", file=sys.stderr)
        return 1
    backend = resolve_backend(args.backend, args.model, args.claude_bare, args.codex_api_key)
    embedding_backend = try_create_default_backend()
    try:
        depth_provider = resolve_depth_provider(
            DepthMode(args.depth_mode), args.depth_command, tuple(args.depth_arg), args.depth_tool,
            args.depth_timeout, args.depth_max_edges,
        )
        with RichProgressReporter() as progress:
            result = index_service(
                conn, args.service, root, detector, backend, force=args.force, progress=progress,
                embedding_backend=embedding_backend, depth_provider=depth_provider,
            )
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"{result.service_name}: status={result.status} files_changed={result.files_changed} "
        f"llm_calls={result.llm_calls} cost_usd={result.cost_usd}"
    )
    return 0 if result.status == "ok" else 1


def _cmd_list(args: argparse.Namespace) -> int:
    conn = open_db(args.db)
    rows = services_repo.list_services(conn)
    if not rows:
        print("(nenhum serviço indexado ainda)")
        return 0
    for r in rows:
        repository = r["repository_name"] or "(standalone)"
        print(
            f"{r['name']:<30} repository={repository:<20} stack={r['stack'] or '?':<14} "
            f"apis={r['api_count']:<3} {r['short_desc'] or ''}"
        )
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    conn = open_db(args.db)
    if args.service:
        row = services_repo.get_service_by_name(conn, args.service)
        if row is None:
            print(f"error: unknown service {args.service!r}", file=sys.stderr)
            return 1
        hashes = indexed_files_repo.get_indexed_file_hashes(conn, row["id"])
        print(f"service: {row['name']} ({row['stack']}) — {row['root_path']}")
        print(f"last_commit: {row['last_commit']}")
        print(f"indexed files: {len(hashes)}")
        for run in index_runs_repo.recent_index_runs(conn, row["id"], limit=5):
            print(
                f"  run#{run['id']} {run['started_at']} status={run['status']} backend={run['backend']} "
                f"files_changed={run['files_changed']} llm_calls={run['llm_calls']} "
                f"tokens=(in={run['input_tokens']},out={run['output_tokens']}) cost_usd={run['cost_usd']} "
                f"notes={run['notes']}"
            )
        totals = index_runs_repo.usage_totals(conn, row["id"])
        print(
            f"cumulative usage: input_tokens={totals['input_tokens']} output_tokens={totals['output_tokens']} "
            f"cost_usd={totals['cost_usd']}"
        )
    else:
        services = services_repo.list_services(conn)
        print(f"services indexed: {len(services)}")
        for run in index_runs_repo.recent_index_runs(conn, limit=10):
            svc = services_repo.get_service_by_id(conn, run["service_id"]) if run["service_id"] else None
            name = svc["name"] if svc else "?"
            print(
                f"  run#{run['id']} service={name} status={run['status']} backend={run['backend']} "
                f"files_changed={run['files_changed']} llm_calls={run['llm_calls']} cost_usd={run['cost_usd']}"
            )
        totals = index_runs_repo.usage_totals(conn)
        print(
            f"cumulative usage: input_tokens={totals['input_tokens']} output_tokens={totals['output_tokens']} "
            f"cost_usd={totals['cost_usd']}"
        )
        verifications = verification_repo.latest_verifications(conn, limit=5)
        if verifications:
            print("recent change surface verifications:")
            for v in verifications:
                print(
                    f"  run#{v['run_id']} repository={v['repository']} since={v['since_commit']} "
                    f"precision={v['precision']} recall={v['recall']}"
                )
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    conn = open_db(args.db)
    if args.format == "mermaid":
        written = export_mermaid(conn, Path(args.out), service_filter=args.service)
    else:
        written = export_markdown(conn, Path(args.out), service_filter=args.service)
    print(f"wrote {len(written)} files under {args.out}")
    return 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    conn = open_db(args.db)
    backend = resolve_backend(args.backend, args.model, args.claude_bare, args.codex_api_key)
    result = change_surface.analyze_change_surface(conn, args.task, backend, hint_services=args.hint_services)
    print(json.dumps(result, indent=2))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    conn = open_db(args.db)
    result = verify_change_surface(conn, args.run_id, args.repository, args.since, record_feedback=args.record_feedback)
    if "error" in result:
        print(f"error: {result['error']}", file=sys.stderr)
        return 1
    print(f"predicted: {result['predicted']}")
    print(f"actual:    {result['actual']}")
    print(f"true_positives:  {result['true_positives']}")
    print(f"false_positives: {result['false_positives']}")
    print(f"false_negatives: {result['false_negatives']}")
    print(f"precision: {result['precision']}")
    print(f"recall:    {result['recall']}")
    if args.record_feedback:
        print("feedback recorded for true/false positives")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from orbitkb.mcp.server import build_server

    backend = resolve_backend(args.backend, args.model, args.claude_bare, args.codex_api_key)
    server = build_server(args.db, backend=backend)
    server.run()
    return 0


_TOP_LEVEL_EPILOG = """\
The orbitkb workflow is index -> ask -> verify:

  1. index   Point it at a repo (or several) so it builds a System Knowledge Model.
  2. ask     Register it as an MCP server (`serve`) and have an agent call
             find_change_surface with an engineering task/epic, before it opens
             any file, to get the likely blast radius with evidence + confidence.
  3. verify  Once the change ships, check whether the prediction was right
             against the real git diff, closing the feedback loop.

Examples:
  orbitkb index ~/code/my-monorepo --repository-name my-monorepo
  orbitkb serve --backend claude
  orbitkb verify 3 --repository my-monorepo --since a1b2c3d

Run `orbitkb <command> --help` for a runnable example of any single command.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orbitkb", epilog=_TOP_LEVEL_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"orbitkb {orbitkb.__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_backend_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--backend", choices=["claude", "codex"], default=None, help="LLM backend to shell out to headless (default: whichever CLI is on PATH)")
        p.add_argument("--model", default=None, help="Override the backend's default model")
        p.add_argument("--claude-bare", action="store_true", help="Use ANTHROPIC_API_KEY billing instead of the Claude CLI subscription session")
        p.add_argument("--codex-api-key", action="store_true", help="Use CODEX_API_KEY billing instead of the ChatGPT subscription session")
        p.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")

    def add_depth_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--depth-mode", choices=[mode.value for mode in DepthMode], default=DepthMode.OFF.value,
            help="Flow depth: off (static analysis only), augment (optional MCP enrichment), or require.",
        )
        p.add_argument("--depth-command", default=None, help="External code-intelligence MCP executable for augment/require mode.")
        p.add_argument("--depth-arg", action="append", default=[], help="One argument for --depth-command; repeat for multiple arguments.")
        p.add_argument("--depth-tool", default="trace_entrypoint", help="MCP tool returning the documented bounded edge payload.")
        p.add_argument("--depth-timeout", type=float, default=15.0, help="Maximum seconds for the external MCP session (default: 15).")
        p.add_argument("--depth-max-edges", type=int, default=100, help="Maximum enriched edges per indexed service (default: 100).")

    p_index = sub.add_parser(
        "index", help="Index a monorepo root or a single service repo",
        epilog=(
            "examples:\n"
            "  orbitkb index ~/code/orders-service\n"
            "  orbitkb index ~/code/my-monorepo --repository-name my-monorepo\n"
            "  orbitkb index . --service custom-name --force\n"
            "  # --stack bypasses auto-detection for a folder no heuristic recognizes\n"
            "  # (e.g. a library/CLI package, not a web microservice):\n"
            "  orbitkb index . --service orbitkb-core --stack python\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_index.add_argument("path", help="A single service's root, or a monorepo root containing several")
    p_index.add_argument("--service", default=None, help="Override the inferred service name (only valid for a single-service path)")
    p_index.add_argument(
        "--stack", default=None, choices=["node-ts", "python", "jvm-spring", "go"],
        help="Explicit stack, bypassing auto-detection entirely — requires --service too. "
             "For a folder shape auto-detection can't recognize (e.g. a library/CLI package).",
    )
    p_index.add_argument("--repository-name", default=None, help="Explicit repository name; avoids collisions when indexing several repos into one shared DB")
    p_index.add_argument("--force", action="store_true", help="Regenerate everything, ignoring file-hash skip")
    add_backend_args(p_index)
    add_depth_args(p_index)
    p_index.set_defaults(func=_cmd_index)

    p_update = sub.add_parser(
        "update", help="Re-index one already-known service by name",
        epilog="example:\n  orbitkb update orders-service\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_update.add_argument("service", help="Exact name shown by `orbitkb list`")
    p_update.add_argument("--force", action="store_true", help="Regenerate everything, ignoring file-hash skip")
    add_backend_args(p_update)
    add_depth_args(p_update)
    p_update.set_defaults(func=_cmd_update)

    p_list = sub.add_parser("list", help="List indexed services")
    p_list.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    p_list.set_defaults(func=_cmd_list)

    p_status = sub.add_parser(
        "status", help="Show indexing status/history, and recent change surface verifications",
        epilog="examples:\n  orbitkb status\n  orbitkb status orders-service\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_status.add_argument("service", nargs="?", default=None, help="Show one service's indexing history instead of the whole DB's")
    p_status.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    p_status.set_defaults(func=_cmd_status)

    p_export = sub.add_parser(
        "export", help="Export the database to Markdown or Mermaid diagrams",
        epilog=(
            "examples:\n"
            "  orbitkb export md --out docs/\n"
            "  orbitkb export mermaid --out docs/\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_export.add_argument("format", choices=["md", "mermaid"], help="md: human-readable docs; mermaid: topology.mmd + one er.mmd per service")
    p_export.add_argument("--out", default="docs", help="Output directory (default: docs)")
    p_export.add_argument("--service", default=None, help="Export only this service instead of every indexed one")
    p_export.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    p_export.set_defaults(func=_cmd_export)

    p_analyze = sub.add_parser(
        "analyze", help="Run find_change_surface for a task and print the result as JSON",
        epilog=(
            "examples:\n"
            "  orbitkb analyze \"Add support for Pix in checkout\"\n"
            "  orbitkb analyze \"Add support for Pix in checkout\" --backend claude --db verify/sample_project.db\n"
            "  orbitkb analyze \"xyz internal cleanup\" --hint-services notification-service\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_analyze.add_argument("task", help="Free-text engineering task/epic, e.g. \"Add support for Pix in checkout\"")
    p_analyze.add_argument("--hint-services", nargs="+", default=None, help="Anchor the search on these services even without a keyword match")
    add_backend_args(p_analyze)
    p_analyze.set_defaults(func=_cmd_analyze)

    p_verify = sub.add_parser(
        "verify", help="Compare a past find_change_surface run against what a repository's commits actually changed",
        epilog=(
            "example:\n"
            "  orbitkb verify 3 --repository my-monorepo --since a1b2c3d --record-feedback\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_verify.add_argument("run_id", type=int, help="run_id from a prior find_change_surface/analyze response")
    p_verify.add_argument("--repository", required=True, help="Repository name, as shown by `orbitkb list`/`--repository-name` at index time")
    p_verify.add_argument("--since", required=True, help="Commit the run was made against; actual changes are `git diff --since..HEAD`")
    p_verify.add_argument("--record-feedback", action="store_true", help="Auto-record confirmed/rejected feedback for the predicted services")
    p_verify.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    p_verify.set_defaults(func=_cmd_verify)

    p_serve = sub.add_parser(
        "serve", help="Run the MCP server (stdio)",
        epilog=(
            "example (register with an MCP client, e.g. Claude CLI/Codex):\n"
            "  orbitkb serve --backend claude\n"
            "  orbitkb serve --db ~/.orbitkb/orbitkb.db --backend codex\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_serve.add_argument("--transport", choices=["stdio"], default="stdio", help="MCP transport (only stdio is supported today)")
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
