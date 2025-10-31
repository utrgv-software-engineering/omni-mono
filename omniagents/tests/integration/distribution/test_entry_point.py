import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from omniagents import __version__

REPO_ROOT = Path(__file__).resolve().parents[3]


def build_env(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    pythonpath_entries = [str(REPO_ROOT)]
    existing = env.get("PYTHONPATH")
    if existing:
        pythonpath_entries.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    env["OMNIAGENTS_HOME"] = str(home)
    return env


def test_module_help_invocation(tmp_path):
    env = build_env(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "omniagents", "--help"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()


def test_module_version_invocation(tmp_path):
    env = build_env(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "omniagents", "--version"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0
    assert __version__ in result.stdout


def test_runpy_entry_point(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("OMNIAGENTS_HOME", str(home))
    monkeypatch.setattr(sys, "argv", ["omniagents", "--help"])

    with pytest.raises(SystemExit) as exc:
        runpy.run_module("omniagents.__main__", run_name="__main__")

    assert exc.value.code == 0
