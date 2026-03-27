"""Shared fixtures for sandbox evaluation tests.

These tests require Docker and a pre-built sandbox image. They are skipped
automatically if Docker is unavailable or the image hasn't been built.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

# ── Skip conditions ──────────────────────────────────────────────────

_SANDBOX_IMAGE = "omni-code-sandbox:latest"


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _image_exists(tag: str) -> bool:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", tag],
            check=False,
            capture_output=True,
        )
        return result.returncode == 0
    except Exception:
        return False


skip_no_docker = pytest.mark.skipif(
    not _docker_available(), reason="Docker not available"
)
skip_no_image = pytest.mark.skipif(
    not _image_exists(_SANDBOX_IMAGE),
    reason=f"Sandbox image {_SANDBOX_IMAGE} not built",
)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def event_loop():
    """Use a single event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def sandbox_env():
    """Provide a SandboxEvalEnvironment for the test session."""
    from environments.sandbox_env import SandboxEvalEnvironment

    return SandboxEvalEnvironment(
        image=_SANDBOX_IMAGE,
        timeout_seconds=180,
    )


@pytest.fixture(scope="session")
def sandbox_scenarios() -> dict[str, list[dict]]:
    """Load all sandbox-only scenario files."""
    scenarios_dir = Path(__file__).resolve().parent.parent.parent / "evaluations" / "scenarios"
    out: dict[str, list[dict]] = {}
    for name in ("worker_file_conflicts", "destructive_action"):
        path = scenarios_dir / f"{name}.yml"
        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f)
            out[name] = data.get("scenarios", [])
    return out
