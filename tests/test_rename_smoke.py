"""Regression guard for the context_insight -> blastmap rename: the installed
console script and the MCP server module path must both resolve under the new name."""
import subprocess
import sys

from blastmap import cli


def test_cli_prog_name_is_blastmap():
    assert cli.build_parser().prog == "blastmap"


def test_installed_console_script_runs_under_new_name():
    result = subprocess.run(["blastmap", "--help"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert "index" in result.stdout and "serve" in result.stdout


def test_mcp_server_module_is_importable_under_new_package():
    result = subprocess.run(
        [sys.executable, "-m", "blastmap.mcp.server", "--help"], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0
    assert "blastmap serve" in result.stdout
