"""Regression guard for the project's identity: distribution name, importable
package and installed console script are all `orbitkb`."""
import subprocess
import sys

from orbitkb import cli


def test_cli_prog_name_is_orbitkb():
    assert cli.build_parser().prog == "orbitkb"


def test_installed_console_script_runs_under_new_name():
    result = subprocess.run(["orbitkb", "--help"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert "index" in result.stdout and "serve" in result.stdout


def test_mcp_server_module_is_importable_under_new_package():
    result = subprocess.run(
        [sys.executable, "-m", "orbitkb.mcp.server", "--help"], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0
    assert "orbitkb serve" in result.stdout
