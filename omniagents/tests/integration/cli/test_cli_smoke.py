import os
import sys
from pathlib import Path

import pytest

from omniagents import __version__
from omniagents.cli import main as cli_main

REPO_ROOT = Path(__file__).resolve().parents[2]


def configure_env(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("OMNIAGENTS_HOME", str(home))
    pythonpath_entries = [str(REPO_ROOT)]
    existing = os.environ.get("PYTHONPATH")
    if existing:
        pythonpath_entries.append(existing)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(pythonpath_entries))


def test_cli_help_displays_usage(monkeypatch, tmp_path, capsys):
    configure_env(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["omniagents", "--help"])

    with pytest.raises(SystemExit) as exc:
        cli_main()

    assert exc.value.code == 0
    captured = capsys.readouterr()
    stdout_lower = captured.out.lower()
    assert "usage:" in stdout_lower
    assert "{new,run,save" in stdout_lower


def test_cli_version_outputs_package_version(monkeypatch, tmp_path, capsys):
    configure_env(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["omniagents", "--version"])

    with pytest.raises(SystemExit) as exc:
        cli_main()

    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert __version__ in captured.out


def test_cli_new_scaffolds_project(monkeypatch, tmp_path, capsys):
    configure_env(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        sys,
        "argv",
        ["omniagents", "new", "smoke_demo", "--dir", str(workspace)],
    )

    with pytest.raises(SystemExit) as exc:
        cli_main()

    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "Created project" in captured.out
    project_dir = workspace / "smoke_demo"
    assert project_dir.is_dir()
    assert (project_dir / "project.yml").is_file()


def test_cli_run_without_project_exits_with_error(monkeypatch, tmp_path, capsys):
    configure_env(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["omniagents", "run"])

    with pytest.raises(SystemExit) as exc:
        cli_main()

    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "Provide --project" in captured.out


def test_cli_invalid_command_reports_error(monkeypatch, tmp_path, capsys):
    configure_env(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["omniagents", "bogus"])

    with pytest.raises(SystemExit) as exc:
        cli_main()

    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "invalid choice" in captured.err.lower()
