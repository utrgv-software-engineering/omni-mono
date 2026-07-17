"""Phase 1: builtin capabilities are auto-discovered into every project's
registry, agents can reference them by name, and project-defined caps with
the same name override the builtin without erroring."""

from __future__ import annotations

import textwrap
from pathlib import Path

from omniagents.core.project.runtime import load_project


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content))


def _make_min_project(tmp_path: Path, project_yaml: str = "name: test\n") -> Path:
    """Build the smallest valid project layout: project.yml + agents/main/agent.yml."""
    _write(tmp_path / "project.yml", project_yaml)
    _write(
        tmp_path / "agents" / "main" / "agent.yml",
        """
        name: main
        instructions: be a test
        """,
    )
    return tmp_path


def test_builtin_capabilities_auto_discovered(tmp_path):
    """Every project (even one that doesn't declare paths.capabilities) gets
    the builtin capability registry populated with filesystem/shell/web/..."""
    _make_min_project(tmp_path)

    proj = load_project(str(tmp_path))

    # The Phase 1 builtins
    assert "filesystem" in proj.capability_registry
    assert "shell" in proj.capability_registry
    assert "web" in proj.capability_registry
    assert "serpapi" in proj.capability_registry
    assert "artifacts" in proj.capability_registry
    # `image` is folded into `filesystem` (read_image is a file op)
    assert "image" not in proj.capability_registry

    # Filesystem bundles file ops + read_image
    fs = proj.capability_registry["filesystem"]
    fs_tool_names = {t.name for t in fs.tools()}
    assert {
        "read_file",
        "read_image",
        "list_directory",
        "glob_files",
        "grep_files",
        "write_file",
        "edit_file",
        "apply_patch",
    }.issubset(fs_tool_names)

    # `web` is provider-agnostic; SerpAPI tools live in their own pack so a
    # project without SERPAPI_API_KEY can still use web_fetch / download_file.
    web_tool_names = {t.name for t in proj.capability_registry["web"].tools()}
    assert web_tool_names == {"web_fetch", "download_file"}
    serpapi_tool_names = {
        t.name for t in proj.capability_registry["serpapi"].tools()
    }
    assert serpapi_tool_names == {"web_search", "youtube_search", "scholar_search"}


def test_project_capability_overrides_builtin(tmp_path):
    """A project-defined capability with the same name as a builtin replaces
    the builtin without error — letting projects customize the standard
    bundles."""
    _make_min_project(
        tmp_path,
        """
        name: test
        capabilities:
          - name: filesystem
            tools: [read_file]
            instructions: |
              custom filesystem with only read access
        """,
    )

    proj = load_project(str(tmp_path))
    fs = proj.capability_registry["filesystem"]
    # User definition wins — only read_file, not the full builtin surface
    assert {t.name for t in fs.tools()} == {"read_file"}
    assert "custom filesystem" in fs.instructions_text


def test_two_project_capabilities_same_name_errors(tmp_path):
    """Defining the same capability name twice on the project side (once
    inline, once in paths.capabilities/) is an error — silent dedup hides
    modeling mistakes. Builtin override is still allowed (different
    precedence path)."""
    import pytest

    _write(
        tmp_path / "capabilities" / "fs.yml",
        """
        name: my_caps
        tools: [read_file]
        """,
    )
    _write(
        tmp_path / "project.yml",
        """
        name: test
        capabilities:
          - name: my_caps
            tools: [list_directory]
        """,
    )
    _write(
        tmp_path / "agents" / "main" / "agent.yml",
        """
        name: main
        instructions: be a test
        """,
    )

    with pytest.raises(ValueError, match="my_caps"):
        load_project(str(tmp_path))


def test_notebook_readonly_and_slash_builtins(tmp_path):
    """The notebook, read-only, and slash packs ship as builtins with the
    expected tool surfaces."""
    _make_min_project(tmp_path)

    proj = load_project(str(tmp_path))

    nb_tools = {t.name for t in proj.capability_registry["notebook"].tools()}
    assert nb_tools == {"read_notebook", "edit_notebook"}
    nb_ro_tools = {
        t.name for t in proj.capability_registry["notebook_readonly"].tools()
    }
    assert nb_ro_tools == {"read_notebook"}

    fs_ro_tools = {
        t.name for t in proj.capability_registry["filesystem_readonly"].tools()
    }
    assert fs_ro_tools == {
        "read_file",
        "read_image",
        "list_directory",
        "glob_files",
        "grep_files",
    }
    sh_ro_tools = {
        t.name for t in proj.capability_registry["shell_readonly"].tools()
    }
    assert sh_ro_tools == {"execute_bash"}

    slash_tools = {t.name for t in proj.capability_registry["slash"].tools()}
    assert slash_tools == {"dispatch"}


def test_agent_uses_builtin_capability(tmp_path):
    """Agent referencing a builtin capability resolves through the project
    registry into the spec's resolved_capabilities."""
    from omniagents.core.project.runtime import build_spec_from_project

    _write(tmp_path / "project.yml", "name: test\n")
    _write(
        tmp_path / "agents" / "main" / "agent.yml",
        """
        name: main
        instructions: be a test
        capabilities: [filesystem, web]
        """,
    )

    proj = load_project(str(tmp_path))
    spec = build_spec_from_project(proj)
    cap_names = [c.capability_name for c in spec.resolved_capabilities]
    assert cap_names == ["filesystem", "web"]
