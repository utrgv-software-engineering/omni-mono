"""Tests for the builtin ``workspace_info`` (fs_*) server functions."""

import asyncio
import os
import textwrap
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from omniagents.builtin.server_functions.workspace_info import (
    fs_get_cwd,
    fs_get_home,
    fs_get_sources,
    fs_get_workspace_root,
    fs_list_dir,
)
from omniagents.core.runtime import serve_state
from omniagents.core.runtime.workspace_info import (
    set_manifest_root_provider,
    set_sandbox_session_provider,
    set_workspace_sources_provider,
)


@pytest.fixture(autouse=True)
def _reset_seams():
    serve_state.reset()
    yield
    set_workspace_sources_provider(None)
    set_manifest_root_provider(None)
    set_sandbox_session_provider(None)
    serve_state.reset()


class _FakeSession:
    def __init__(self, context=None):
        self.id = "sess-1"
        self.context = context


# ----------------------------- fs_get_cwd / fs_get_home ----------------- #


def test_fs_get_cwd_returns_process_cwd():
    assert asyncio.run(fs_get_cwd()) == {"path": os.getcwd()}


def test_fs_get_home_returns_home():
    assert asyncio.run(fs_get_home()) == {"path": os.path.expanduser("~")}


# ----------------------------- fs_get_workspace_root -------------------- #


def test_workspace_root_prefers_session_context(tmp_path):
    session = _FakeSession(context={"workspace_root": str(tmp_path)})
    result = asyncio.run(fs_get_workspace_root(session))
    assert result == {"path": str(tmp_path)}


def test_workspace_root_falls_back_to_manifest_root_seam():
    set_manifest_root_provider(lambda: "/workspace")
    result = asyncio.run(fs_get_workspace_root(_FakeSession()))
    assert result == {"path": "/workspace"}


def test_workspace_root_defaults_to_cwd_standalone():
    # No session workspace_root, no manifest-root provider installed.
    result = asyncio.run(fs_get_workspace_root(_FakeSession()))
    assert result == {"path": os.getcwd()}


# ----------------------------- fs_get_sources --------------------------- #


def test_sources_default_empty_without_provider():
    assert asyncio.run(fs_get_sources()) == {"sources": []}


def test_sources_uses_installed_provider():
    sources = [{"kind": "local", "mountName": "app", "path": "/host/app"}]
    set_workspace_sources_provider(lambda: sources)
    result = asyncio.run(fs_get_sources())
    assert result == {"sources": sources}
    # Shallow copy — mutating the result must not touch the provider's list.
    result["sources"].append({"kind": "local"})
    assert len(sources) == 1


def test_sources_default_to_published_serve_state():
    serve_state.set_sources(
        [{"kind": "local", "mountName": "app", "path": "/host/app"}]
    )
    result = asyncio.run(fs_get_sources())
    assert result == {
        "sources": [{"kind": "local", "mountName": "app", "path": "/host/app"}]
    }


def test_installed_sources_provider_wins_over_serve_state():
    serve_state.set_sources([{"kind": "local", "mountName": "published"}])
    set_workspace_sources_provider(lambda: [{"kind": "local", "mountName": "mine"}])
    result = asyncio.run(fs_get_sources())
    assert result == {"sources": [{"kind": "local", "mountName": "mine"}]}


def test_workspace_root_defaults_to_published_serve_state():
    serve_state.set_manifest_root("/workspace")
    result = asyncio.run(fs_get_workspace_root(_FakeSession()))
    assert result == {"path": "/workspace"}


def test_installed_manifest_root_provider_wins_over_serve_state():
    serve_state.set_manifest_root("/published")
    set_manifest_root_provider(lambda: "/mine")
    result = asyncio.run(fs_get_workspace_root(_FakeSession()))
    assert result == {"path": "/mine"}


def test_sandbox_session_defaults_to_published_serve_state():
    from omniagents.core.runtime.workspace_info import get_sandbox_session

    session = object()
    serve_state.set_sandbox_session(session)
    assert get_sandbox_session() is session

    override = object()
    set_sandbox_session_provider(lambda: override)
    assert get_sandbox_session() is override


def test_sources_swallow_provider_errors():
    def _boom():
        raise RuntimeError("embedder hook broke")

    set_workspace_sources_provider(_boom)
    assert asyncio.run(fs_get_sources()) == {"sources": []}


# ----------------------------- fs_list_dir (host) ----------------------- #


def _make_tree(tmp_path: Path) -> Path:
    (tmp_path / "beta").mkdir()
    (tmp_path / "alpha").mkdir()
    (tmp_path / ".hidden_dir").mkdir()
    (tmp_path / "note.txt").write_text("hi")
    (tmp_path / ".dotfile").write_text("x")
    return tmp_path


def test_list_dir_host_dirs_only_by_default(tmp_path):
    _make_tree(tmp_path)
    result = asyncio.run(fs_list_dir(path=str(tmp_path)))
    assert result["path"] == str(tmp_path)
    assert result["parent"] == str(tmp_path.parent)
    names = [e["name"] for e in result["entries"]]
    assert names == ["alpha", "beta"]  # sorted, no files, no hidden


def test_list_dir_host_include_files_and_hidden(tmp_path):
    _make_tree(tmp_path)
    result = asyncio.run(
        fs_list_dir(path=str(tmp_path), include_files=True, ignore_hidden=False)
    )
    names = [e["name"] for e in result["entries"]]
    # Dirs first, then files, each alphabetical.
    assert names == [".hidden_dir", "alpha", "beta", ".dotfile", "note.txt"]
    by_name = {e["name"]: e for e in result["entries"]}
    assert by_name["note.txt"]["is_dir"] is False
    assert by_name["note.txt"]["size"] == 2


def test_list_dir_host_missing_path_raises(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        asyncio.run(fs_list_dir(path=str(tmp_path / "nope")))


def test_list_dir_host_file_path_raises(tmp_path):
    _make_tree(tmp_path)
    with pytest.raises(ValueError, match="Not a directory"):
        asyncio.run(fs_list_dir(path=str(tmp_path / "note.txt")))


# ----------------------------- fs_list_dir (sandbox) -------------------- #


class _FakeSandboxSession:
    """Duck-types the SDK sandbox session surface SandboxWorkspace.list_dir
    uses: ``await session.ls(path, user=...)``."""

    def __init__(self, entries):
        self._entries = entries
        self.ls_calls = []

    async def ls(self, path, user=None):
        self.ls_calls.append(str(path))
        return self._entries


def _sdk_entry(name, is_dir, size=None):
    return SimpleNamespace(
        name=name, path=name, is_dir=is_dir, size=size, mtime=datetime(2026, 1, 2)
    )


def test_list_dir_browses_sandbox_when_session_live():
    session = _FakeSandboxSession(
        [
            _sdk_entry("src", True),
            _sdk_entry("readme.md", False, size=10),
            _sdk_entry(".git", True),
        ]
    )
    set_sandbox_session_provider(lambda: session)
    set_manifest_root_provider(lambda: "/workspace")

    result = asyncio.run(fs_list_dir(path="/workspace/sub"))

    assert session.ls_calls == ["/workspace/sub"]
    assert result["path"] == "/workspace/sub"
    # Parent stays exposed while within the manifest root.
    assert result["parent"] == "/workspace"
    # Directories only (default), hidden filtered, container paths.
    assert [e["path"] for e in result["entries"]] == ["/workspace/sub/src"]


def test_list_dir_sandbox_clamps_host_paths_to_manifest_root():
    session = _FakeSandboxSession([_sdk_entry("src", True)])
    set_sandbox_session_provider(lambda: session)
    set_manifest_root_provider(lambda: "/workspace")

    # A stray host path (outside the manifest root) lists the root instead.
    result = asyncio.run(fs_list_dir(path="/home/someone/project"))

    assert result["path"] == "/workspace"
    # No "up" above the workspace boundary.
    assert result["parent"] is None


def test_list_dir_sandbox_include_files():
    session = _FakeSandboxSession(
        [_sdk_entry("src", True), _sdk_entry("readme.md", False, size=10)]
    )
    set_sandbox_session_provider(lambda: session)
    set_manifest_root_provider(lambda: "/workspace")

    result = asyncio.run(fs_list_dir(path="/workspace", include_files=True))

    entries = {e["name"]: e for e in result["entries"]}
    assert set(entries) == {"src", "readme.md"}
    assert entries["readme.md"]["size"] == 10
    assert entries["readme.md"]["modified"] == "2026-01-02T00:00:00"


# ----------------------------- capability pack -------------------------- #


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content))


def _make_min_project(tmp_path: Path) -> Path:
    _write(tmp_path / "project.yml", "name: test\n")
    _write(
        tmp_path / "agents" / "main" / "agent.yml",
        """
        name: main
        instructions: be a test
        """,
    )
    return tmp_path


def test_workspace_info_pack_exposes_exact_rpc_surface(tmp_path):
    """The web UI feature-detects these names (App.tsx / WorkspacePicker.tsx)
    — the pack must expose them verbatim."""
    from omniagents.core.project.runtime import load_project

    proj = load_project(str(_make_min_project(tmp_path)))

    assert "workspace_info" in proj.capability_registry
    cap = proj.capability_registry["workspace_info"]
    names = [m["name"] for m in cap.server_functions()]
    assert names == [
        "fs_get_cwd",
        "fs_get_workspace_root",
        "fs_get_home",
        "fs_list_dir",
        "fs_get_sources",
    ]
    assert cap.tools() == []
    # Builtin server functions stay capability-gated.
    assert "fs_list_dir" in proj.builtin_server_function_names


def test_project_server_function_shadows_builtin(tmp_path):
    """A project-local server function with the same name overrides the
    builtin and is no longer treated as capability-gated builtin."""
    from omniagents.core.project.runtime import load_project

    _make_min_project(tmp_path)
    _write(
        tmp_path / "server_functions" / "custom_fs.py",
        """
        from omniagents import server_function

        @server_function(description="project fs_list_dir", strict=True)
        async def fs_list_dir() -> dict:
            return {"path": "shadowed", "parent": None, "entries": []}
        """,
    )

    proj = load_project(str(tmp_path))

    meta = proj.server_functions_registry["fs_list_dir"]
    assert meta["description"] == "project fs_list_dir"
    assert "fs_list_dir" not in proj.builtin_server_function_names
    # Untouched builtins keep their gating.
    assert "fs_get_home" in proj.builtin_server_function_names
