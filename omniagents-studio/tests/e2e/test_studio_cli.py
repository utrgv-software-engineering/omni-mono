"""Smoke tests for the ``omniagents-studio`` command line."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from omniagents_studio.cli import main as cli_main

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "studio_cli"


def copy_fixture(tmp_path, name: str) -> Path:
    destination = tmp_path / name
    shutil.copytree(FIXTURES_ROOT / name, destination)
    return destination


def build_env(tmp_path) -> dict:
    env = os.environ.copy()
    env["OMNIAGENTS_HOME"] = str(tmp_path / "home")
    return env


def run_cli(args, *, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "omniagents_studio.cli", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
        check=False,
    )


def test_studio_cli_show_and_export(tmp_path):
    project_dir = copy_fixture(tmp_path, "minimal")
    env = build_env(tmp_path)
    manifest = str(project_dir / "project.yml")

    show_result = run_cli(["show", "--project", manifest], cwd=project_dir, env=env)
    assert show_result.returncode == 0, show_result.stderr
    assert "studio_cli_demo" in show_result.stdout
    assert "omniagents-studio serve --project studio_cli_demo" in show_result.stdout

    traces_db = Path(env["OMNIAGENTS_HOME"]) / "traces" / "studio_cli_demo.db"
    traces_db.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(str(traces_db)).close()

    export_path = project_dir / "export.json"
    export_result = run_cli(
        ["export", str(export_path), "--project", manifest], cwd=project_dir, env=env
    )
    assert export_result.returncode == 0, export_result.stderr
    assert "Exported" in export_result.stdout
    assert isinstance(json.loads(export_path.read_text()), list)


def test_studio_cli_export_requires_project(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)

    try:
        cli_main(["export", "out.json"])
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover - the command must exit
        raise AssertionError("export without a project must exit")
    assert "Provide --project" in capsys.readouterr().out


def test_studio_cli_serve_respects_flags(monkeypatch, tmp_path):
    project_dir = copy_fixture(tmp_path, "minimal")
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "home"))

    captured = {}

    def fake_serve(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("omniagents_studio.server.serve", fake_serve)

    cli_main(
        [
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
    )

    assert captured["project"] == "studio_cli_demo"
    assert captured["manifest_path"] == str(project_dir / "project.yml")
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8765
    assert captured["dev"] is True
    assert captured["dev_host"] == "0.0.0.0"
    assert captured["dev_port"] == 4242
    assert captured["db_path"] is None


def test_studio_cli_serve_uses_cwd_manifest(monkeypatch, tmp_path):
    project_dir = copy_fixture(tmp_path, "minimal")
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(project_dir)

    captured = {}
    monkeypatch.setattr(
        "omniagents_studio.server.serve", lambda **kwargs: captured.update(kwargs)
    )

    cli_main(["serve"])

    assert captured["project"] == "studio_cli_demo"
    assert captured["manifest_path"] == str(project_dir / "project.yml")
