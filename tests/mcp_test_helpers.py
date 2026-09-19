"""Shared helpers for tests that drive the real MCP server over stdio."""
import json
import os
import sys

from mcp import StdioServerParameters


def content_json(result) -> dict:
    return json.loads(result.content[0].text)


def server_params(db_path, *extra_args: str) -> StdioServerParameters:
    """Build stdio params for the real MCP server subprocess.

    mcp's stdio_client only inherits a safe allowlist of env vars into the child
    process, dropping COVERAGE_PROCESS_START — so without passing it through
    explicitly here, the server subprocess would run outside coverage measurement
    even though these tests genuinely exercise it end-to-end.
    """
    env = {}
    if "COVERAGE_PROCESS_START" in os.environ:
        env["COVERAGE_PROCESS_START"] = os.environ["COVERAGE_PROCESS_START"]
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "orbitkb.mcp.server", "--db", str(db_path), *extra_args],
        env=env,
    )
