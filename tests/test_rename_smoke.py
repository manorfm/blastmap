"""Regression guard for the project's identity: distribution name, importable
package and installed console script are all `impactmesh`."""
import subprocess
import sys
from pathlib import Path

from impactmesh import cli


def test_cli_prog_name_is_impactmesh():
    assert cli.build_parser().prog == "impactmesh"


def test_installed_console_script_runs_under_new_name():
    executable = Path(sys.executable).with_name("impactmesh")
    result = subprocess.run([str(executable), "--help"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert "index" in result.stdout and "serve" in result.stdout


def test_mcp_server_module_is_importable_under_new_package():
    result = subprocess.run(
        [sys.executable, "-m", "impactmesh.mcp.server", "--help"], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0
    assert "impactmesh serve" in result.stdout
