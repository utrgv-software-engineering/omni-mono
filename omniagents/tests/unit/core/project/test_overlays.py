"""Tests for project.yml overlay layering."""

from pathlib import Path
from textwrap import dedent

from omniagents.core.project.runtime import load_project


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content))
    return path


def _base_project(tmp_path) -> Path:
    _write(
        tmp_path / "agents" / "main" / "agent.yml",
        """
        name: main
        instructions: You are a test agent.
        tools: []
        """,
    )
    return _write(
        tmp_path / "project.yml",
        """
        name: overlay_test_product
        allow_user_mcp_servers: true
        tracing:
          provider: studio
          project: overlay_test_product
        """,
    )


def test_no_overlays_is_identity(tmp_path, monkeypatch):
    monkeypatch.delenv("OMNIAGENTS_PROJECT_OVERLAYS", raising=False)
    proj = load_project(str(_base_project(tmp_path)))
    assert proj.raw["allow_user_mcp_servers"] is True


def test_env_overlay_deep_merges(tmp_path, monkeypatch):
    base = _base_project(tmp_path)
    overlay = _write(
        tmp_path / "platform.yml",
        """
        allow_user_mcp_servers: false
        security:
          safety_mode: enforced
        tracing:
          settings:
            resume: never
        """,
    )
    monkeypatch.setenv("OMNIAGENTS_PROJECT_OVERLAYS", str(overlay))

    proj = load_project(str(base))
    # Overlay wins on conflicts...
    assert proj.raw["allow_user_mcp_servers"] is False
    # ...adds new sections...
    assert proj.raw["security"]["safety_mode"] == "enforced"
    # ...and deep-merges instead of replacing sibling keys.
    assert proj.raw["tracing"]["provider"] == "studio"
    assert proj.raw["tracing"]["settings"]["resume"] == "never"


def test_config_dir_project_d_layer(tmp_path, monkeypatch):
    base = _base_project(tmp_path)
    cfg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.setenv("APPDATA", str(cfg))  # Windows config root
    monkeypatch.delenv("OMNIAGENTS_PROJECT_OVERLAYS", raising=False)
    _write(
        cfg / "overlay_test_product" / "project.d" / "10-user.yml",
        "allow_user_mcp_servers: false\n",
    )
    _write(
        cfg / "overlay_test_product" / "project.d" / "20-user.yml",
        "allow_user_mcp_servers: true\n",
    )

    proj = load_project(str(base))
    # Lexical order: 20- wins over 10-.
    assert proj.raw["allow_user_mcp_servers"] is True


def test_explicit_param_applies_last(tmp_path, monkeypatch):
    base = _base_project(tmp_path)
    monkeypatch.delenv("OMNIAGENTS_PROJECT_OVERLAYS", raising=False)
    overlay = _write(tmp_path / "o.yml", "allow_user_mcp_servers: false\n")

    proj = load_project(str(base), overlays=[overlay])
    assert proj.raw["allow_user_mcp_servers"] is False


def test_missing_overlay_paths_are_skipped(tmp_path, monkeypatch):
    base = _base_project(tmp_path)
    monkeypatch.setenv(
        "OMNIAGENTS_PROJECT_OVERLAYS", str(tmp_path / "does-not-exist.yml")
    )
    proj = load_project(str(base))
    assert proj.raw["allow_user_mcp_servers"] is True
