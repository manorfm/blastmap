"""Regression guard for the project's identity: the installed console script is
`context-insight` (kebab-case, matching the distribution name), while the importable
package stays `context_insight` (a valid Python identifier)."""
import subprocess
import sys

from context_insight import cli


def test_cli_prog_name_is_context_insight():
    assert cli.build_parser().prog == "context-insight"


def test_installed_console_script_runs_under_new_name():
    result = subprocess.run(["context-insight", "--help"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert "index" in result.stdout and "serve" in result.stdout


def test_mcp_server_module_is_importable_under_new_package():
    result = subprocess.run(
        [sys.executable, "-m", "context_insight.mcp.server", "--help"], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0
    assert "context-insight serve" in result.stdout
