"""Smoke tests for omniagents studio CLI commands. Requires studio extras."""

import builtins
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import sqlite3

import pytest

from omniagents.cli import main as cli_main
import omniagents

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_ROOT = REPO_ROOT / "tests" / "fixtures" / "studio_cli"

CLI_EXTRAS_AVAILABLE = importlib.util.find_spec("omniagents.studio.tracing") is not None

pytestmark = pytest.mark.skipif(
    not CLI_EXTRAS_AVAILABLE, reason="Requires omniagents[studio] extras"
)


def copy_fixture(tmp_path, name: str) -> Path:
    source = FIXTURES_ROOT / name
    destination = tmp_path / name
    shutil.copytree(source, destination)
    return destination


def build_env(tmp_path) -> dict:
    env = os.environ.copy()
    home = tmp_path / "home"
    env["OMNIAGENTS_HOME"] = str(home)
    pythonpath = env.get("PYTHONPATH")
    parts = [str(REPO_ROOT)]
    if pythonpath:
        parts.append(pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def test_studio_cli_show_and_export(tmp_path):
    project_dir = copy_fixture(tmp_path, "minimal")
    env = build_env(tmp_path)

    show_cmd = [
        sys.executable,
        "-m",
        "omniagents.cli",
        "studio",
        "show",
        "--project",
        str(project_dir / "project.yml"),
    ]
    show_result = subprocess.run(
        show_cmd,
        capture_output=True,
        text=True,
        cwd=str(project_dir),
        env=env,
        check=False,
    )
    assert show_result.returncode == 0
    assert "studio_cli_demo" in show_result.stdout
    assert "omniagents studio serve --project studio_cli_demo" in show_result.stdout

    traces_db = Path(env["OMNIAGENTS_HOME"]) / "traces" / "studio_cli_demo.db"
    traces_db.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(str(traces_db)).close()

    export_path = project_dir / "export.json"
    export_cmd = [
        sys.executable,
        "-m",
        "omniagents.cli",
        "studio",
        "export",
        str(export_path),
        "--project",
        str(project_dir / "project.yml"),
    ]
    export_result = subprocess.run(
        export_cmd,
        capture_output=True,
        text=True,
        cwd=str(project_dir),
        env=env,
        check=False,
    )
    assert export_result.returncode == 0
    assert "Exported" in export_result.stdout
    payload = json.loads(export_path.read_text())
    assert isinstance(payload, list)


def test_studio_cli_serve_respects_flags(monkeypatch, tmp_path):
    project_dir = copy_fixture(tmp_path, "minimal")
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))

    captured = {}

    def fake_serve(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("omniagents.studio.tracing.serve", fake_serve)

    argv = [
        "omniagents",
        "studio",
        "serve",
        "--project",
        str(project_dir / "project.yml"),
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
        "--dev",
        "--dev-host",
        "0.0.0.0",
        "--dev-port",
        "4242",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    assert captured["project"] == "studio_cli_demo"
    assert captured["manifest_path"] == str(project_dir / "project.yml")
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8765
    assert captured["dev"] is True
    assert captured["dev_host"] == "0.0.0.0"
    assert captured["dev_port"] == 4242
    assert captured["db_path"] is None


def test_studio_cli_missing_extras(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))

    monkeypatch.delitem(sys.modules, "omniagents.studio", raising=False)
    monkeypatch.delitem(sys.modules, "omniagents.studio.tracing", raising=False)

    attempts = []
    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("omniagents.studio"):
            attempts.append(name)
            raise ImportError("studio extras missing")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(sys, "argv", ["omniagents", "studio", "show"])

    with pytest.raises(SystemExit) as exc:
        cli_main()

    assert exc.value.code == 1
    stdout = capsys.readouterr().out
    assert "Install optional dependencies" in stdout
    assert attempts
