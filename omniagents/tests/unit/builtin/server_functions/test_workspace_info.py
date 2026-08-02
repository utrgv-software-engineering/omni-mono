"""Tests for the builtin ``workspace_info`` (fs_*) server functions."""

import asyncio
import textwrap
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from omniagents.builtin.server_functions.workspace_info import (
    fs_get_cwd,
    fs_get_home,
    fs_get_sources,
    fs_get_workspace_root,
    fs_list_dir,
)
from omniagents.execution import (
    EnvironmentCapability,
    EnvironmentKind,
    EnvironmentLease,
    EnvironmentState,
    ExecutionEnvironmentDescriptor,
    OperationExecutionDescriptor,
    RequestContext,
    WorkspaceResource,
    bind_request_context,
    reset_request_context,
)
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope
from omniagents.workspace.sandbox import SandboxWorkspace
from omniagents.workspace.types import ExecResult, FileEntry


def _workspace(root: str):
    workspace = Mock()
    workspace.root = root

    def resolve(path):
        if path in (".", root):
            return root
        if str(path).startswith("/"):
            if str(path).startswith(f"{root}/"):
                return str(path)
            raise ValueError("outside workspace")
        return f"{root}/{path}"

    workspace.resolve.side_effect = resolve
    workspace.list_dir = AsyncMock(return_value=[])
    workspace.exec = AsyncMock(
        return_value=ExecResult("/environment/home", "", 0, 0.01)
    )
    return workspace


@contextmanager
def _routed(workspace, *, sources=()):
    descriptor = ExecutionEnvironmentDescriptor(
        environment_id="env-1",
        workspace_id="workspace-1",
        kind=(
            EnvironmentKind.DEVBOX
            if isinstance(workspace, SandboxWorkspace)
            else EnvironmentKind.HOST
        ),
        state=EnvironmentState.READY,
        generation=3,
        capabilities=frozenset(
            {
                EnvironmentCapability.WORKSPACE_READ,
                EnvironmentCapability.PROCESS,
            }
        ),
    )
    lease = EnvironmentLease(descriptor, workspace)
    token = bind_request_context(
        RequestContext(
            operation=OperationExecutionDescriptor(
                name="fs.info",
                resource_scope=ResourceScope.WORKSPACE,
                execution_requirements=ExecutionRequirement.WORKSPACE_READ,
            ),
            principal=None,
            workspace=WorkspaceResource("workspace-1", sources=tuple(sources)),
            environment=descriptor,
            environment_lease=lease,
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


def test_workspace_identity_comes_from_lease(tmp_path):
    with _routed(_workspace(str(tmp_path))):
        assert asyncio.run(fs_get_cwd()) == {"path": str(tmp_path)}
        assert asyncio.run(fs_get_workspace_root()) == {"path": str(tmp_path)}


def test_home_executes_in_leased_environment(tmp_path):
    workspace = _workspace(str(tmp_path))
    with _routed(workspace):
        result = asyncio.run(fs_get_home())
    assert result == {"path": "/environment/home"}
    workspace.exec.assert_awaited_once()


def test_sources_come_from_routed_workspace_resource(tmp_path):
    sources = ({"kind": "local", "mountName": "app", "path": "/host/app"},)
    with _routed(_workspace(str(tmp_path)), sources=sources):
        result = asyncio.run(fs_get_sources())
    assert result == {"sources": list(sources)}


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
    workspace = _workspace(str(tmp_path))
    workspace.list_dir.return_value = [
        FileEntry("beta", "beta", True),
        FileEntry("alpha", "alpha", True),
        FileEntry("note.txt", "note.txt", False, size=2),
    ]
    with _routed(workspace):
        result = asyncio.run(fs_list_dir())
    assert result["path"] == str(tmp_path)
    assert result["parent"] is None
    names = [e["name"] for e in result["entries"]]
    assert names == ["alpha", "beta"]  # sorted, no files, no hidden


def test_list_dir_host_include_files_and_hidden(tmp_path):
    _make_tree(tmp_path)
    workspace = _workspace(str(tmp_path))
    workspace.list_dir.return_value = [
        FileEntry(".hidden_dir", ".hidden_dir", True),
        FileEntry("alpha", "alpha", True),
        FileEntry("beta", "beta", True),
        FileEntry(".dotfile", ".dotfile", False, size=1),
        FileEntry("note.txt", "note.txt", False, size=2),
    ]
    with _routed(workspace):
        result = asyncio.run(fs_list_dir(include_files=True, ignore_hidden=False))
    names = [e["name"] for e in result["entries"]]
    # Dirs first, then files, each alphabetical.
    assert names == [".hidden_dir", "alpha", "beta", ".dotfile", "note.txt"]
    by_name = {e["name"]: e for e in result["entries"]}
    assert by_name["note.txt"]["is_dir"] is False
    assert by_name["note.txt"]["size"] == 2


def test_list_dir_propagates_workspace_error(tmp_path):
    workspace = _workspace(str(tmp_path))
    workspace.list_dir.side_effect = FileNotFoundError("nope")
    with _routed(workspace), pytest.raises(ValueError, match="nope"):
        asyncio.run(fs_list_dir(path="nope"))


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
    with _routed(SandboxWorkspace(session, root="/workspace")):
        result = asyncio.run(fs_list_dir(path="/workspace/sub"))

    assert session.ls_calls == ["/workspace/sub"]
    assert result["path"] == "/workspace/sub"
    # Parent stays exposed while within the manifest root.
    assert result["parent"] == "/workspace"
    # Directories only (default), hidden filtered, container paths.
    assert [e["path"] for e in result["entries"]] == ["/workspace/sub/src"]


def test_list_dir_sandbox_rejects_host_paths_outside_lease():
    session = _FakeSandboxSession([_sdk_entry("src", True)])
    with _routed(SandboxWorkspace(session, root="/workspace")):
        with pytest.raises(ValueError, match="outside workspace"):
            asyncio.run(fs_list_dir(path="/home/someone/project"))


def test_list_dir_sandbox_include_files():
    session = _FakeSandboxSession(
        [_sdk_entry("src", True), _sdk_entry("readme.md", False, size=10)]
    )
    with _routed(SandboxWorkspace(session, root="/workspace")):
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
        from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope

        @server_function(
            resource_scope=ResourceScope.WORKSPACE,
            execution_requirements=ExecutionRequirement.WORKSPACE_READ,
            description="project fs_list_dir",
            strict=True,
        )
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
