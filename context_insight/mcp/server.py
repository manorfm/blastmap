from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from context_insight.db.connection import open_db
from context_insight.mcp import queries


def build_server(db_path: Path | None = None) -> MCPServer:
    mcp = MCPServer("context-insight")

    def _conn() -> sqlite3.Connection:
        # A fresh short-lived connection per call: tool handlers may run on different
        # worker threads, and sqlite3 connections aren't safe to share across threads.
        return open_db(db_path)

    @mcp.tool()
    def list_services() -> dict:
        """List every indexed microservice with a one-line description. Call this first."""
        with closing(_conn()) as conn:
            return queries.list_services(conn)

    @mcp.tool()
    def describe_service(service: str) -> dict:
        """Full picture of one microservice: description, why it calls other services/queues
        (with the business reason and data needed), its APIs as one-liners, and what it
        persists/publishes by name only (use describe_persistence/describe_messages for the
        full field-level schema)."""
        with closing(_conn()) as conn:
            return queries.describe_service(conn, service)

    @mcp.tool()
    def list_apis(service: str) -> dict:
        """List the APIs of one microservice with one-line summaries."""
        with closing(_conn()) as conn:
            return queries.list_apis(conn, service)

    @mcp.tool()
    def describe_api(service: str, method: str, path: str) -> dict:
        """Full detail of one API: response shape, calls to other services/queues (with the
        business reason and exact data needed), and validation/authorization rules."""
        with closing(_conn()) as conn:
            return queries.describe_api(conn, service, method, path)

    @mcp.tool()
    def describe_persistence(service: str) -> dict:
        """Full field-level schema of everything one microservice persists."""
        with closing(_conn()) as conn:
            return queries.describe_persistence(conn, service)

    @mcp.tool()
    def describe_messages(service: str) -> dict:
        """Full shape of async messages one microservice publishes/consumes."""
        with closing(_conn()) as conn:
            return queries.describe_messages(conn, service)

    @mcp.tool()
    def search(query: str) -> dict:
        """Keyword search across services, APIs, persistence entities and messages. Use this
        when you don't know which service/API you're looking for."""
        with closing(_conn()) as conn:
            return queries.search(conn, query)

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(prog="context-insight serve")
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()
    server = build_server(args.db)
    server.run()


if __name__ == "__main__":
    main()
