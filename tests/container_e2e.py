"""Small Docker Compose adapter kept out of OrbitKB's production package."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPOSITORY_ROOT / "tests" / "containers" / "compose.yaml"


def require_container_e2e() -> None:
    """Skip clearly unless the caller deliberately enabled container side effects."""
    if os.environ.get("ORBITKB_CONTAINER_E2E") != "1":
        pytest.skip("container E2E is opt-in; set ORBITKB_CONTAINER_E2E=1")
    if shutil.which("docker") is None:
        pytest.skip("container E2E requested but Docker is not installed")
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        pytest.skip("container E2E requested but the Docker daemon is unavailable")


class ContainerStack:
    """Owns one ephemeral Compose lifecycle and its command boundary."""

    def start(self) -> None:
        self._run("up", "--detach", "--wait")

    def stop(self) -> None:
        self._run("down", "--volumes", "--remove-orphans")

    def exec(self, service: str, *command: str) -> str:
        return self._run("exec", "-T", service, *command)

    def port(self, service: str, container_port: int) -> int:
        published = self._run("port", service, str(container_port)).strip().splitlines()[0]
        return int(published.rsplit(":", 1)[1])

    def _run(self, *command: str) -> str:
        completed = subprocess.run(
            ["docker", "compose", "--file", str(COMPOSE_FILE), *command],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"docker compose {' '.join(command)} failed: {completed.stderr.strip()}")
        return completed.stdout
