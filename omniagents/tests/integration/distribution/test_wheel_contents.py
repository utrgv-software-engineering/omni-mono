"""Distribution-level checks for the launcher-facing product modules.

Source-tree imports do not prove that setuptools included a module in the
wheel.  Omni Code's console scripts import these modules before any product
code runs, so omitting either one makes a freshly installed Desktop runtime
unstartable.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

REPO_ROOT = Path(__file__).resolve().parents[3]
PRODUCT_MODULES = (
    "omniagents/product_cli.py",
    "omniagents/product_serve.py",
)


def _build_wheel(output_dir: Path) -> Path:
    """Build with the configured PEP 517 backend, without downloading tools."""

    script = """
from pathlib import Path
from setuptools.build_meta import build_wheel

output_dir = Path(__import__('sys').argv[1])
wheel_name = build_wheel(str(output_dir))
print(output_dir / wheel_name)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(output_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    wheels = list(output_dir.glob("omniagents-*.whl"))
    assert len(wheels) == 1, result.stdout + result.stderr
    return wheels[0]


def test_wheel_ships_importable_product_cli(tmp_path):
    wheel = _build_wheel(tmp_path / "wheelhouse")

    with ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert set(PRODUCT_MODULES) <= names

    # Put only the wheel ahead of the test environment.  Running outside the
    # checkout prevents the source tree from satisfying these imports.
    env = os.environ.copy()
    env["PYTHONPATH"] = str(wheel)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import omniagents.product_cli as cli; "
                "import omniagents.product_serve as serve; "
                "assert '.whl/' in cli.__file__, cli.__file__; "
                "assert '.whl/' in serve.__file__, serve.__file__"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
