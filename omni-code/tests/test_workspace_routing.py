"""Workspace-routing tests for omni-code tools.

After the workspace migration each tool body that touches the filesystem
or shell routes through ``omniagents.workspace.*``. Installing a
recording fake workspace proves that the same body works against a
SandboxWorkspace too — the migrated tools never reach the host
filesystem directly.

Covered tools:
- ``execute_bash`` (foreground): ``workspace.exec``
- ``read_notebook`` / ``edit_notebook``: ``workspace.read_text`` /
  ``workspace.write_text``
- ``convert_to_markdown``: ``workspace.exists`` + ``workspace.read_bytes``
  for non-host backends (host mode goes straight to the path)
- ``worker_tools`` git helpers (``_is_git_repo``, ``_is_git_dirty``):
  ``workspace.exec``
- ``read_job_log`` (used by ``bash_logs`` and ``bash_jobs.tail``):
  ``workspace.read_bytes``

Background-mode log setup and spawn route through the workspace; the
in-memory job registry remains process-local.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, List, Tuple

import pytest
from agents.tool import ToolContext
from agents.usage import Usage

import importlib

import omniagents.builtin.tools.bash_tool as bash_tool

# import_module: the package re-exports the tool object under the same
# name as its module, so a plain ``import ... as`` would bind the
# FunctionTool instead of the module.
conversion_tools = importlib.import_module(
    "omniagents.builtin.tools.convert_to_markdown"
)
import omniagents.builtin.tools.notebook_tools as notebook_tools
import omni_code.workers_runtime as worker_tools
import omniagents.core.workers.isolation as worker_isolation
from omniagents.builtin.tools import _bash_jobs
from omniagents.workspace import (
    ExecResult,
    Workspace,
    WorkspaceEscapeError,
    reset_workspace,
    set_workspace,
)


class _RecordingWorkspace(Workspace):
    """Workspace fake backed by an in-memory file map."""

    def __init__(self, files: dict[str, str] | None = None, root: str = "/fake"):
        self.root = root
        self.files: dict[str, str] = files or {}
        self.calls: List[Tuple[str, tuple, dict]] = []

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def resolve(self, path: str) -> str:
        self._record("resolve", path)
        if path.startswith(self.root) or path.startswith("/"):
            return path
        return f"{self.root}/{path.lstrip('/')}"

    async def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        self._record("read_text", path, encoding=encoding)
        if path in self.files:
            return self.files[path]
        raise FileNotFoundError(path)

    async def read_bytes(self, path: str) -> bytes:
        self._record("read_bytes", path)
        return self.files.get(path, "").encode("utf-8")

    async def write_text(self, path: str, content: str, **kwargs: Any) -> None:
        self._record("write_text", path, content, **kwargs)
        self.files[path] = content

    async def write_bytes(self, path: str, data: bytes, **kwargs: Any) -> None:
        self._record("write_bytes", path, data, **kwargs)

    async def exists(self, path: str) -> bool:
        self._record("exists", path)
        return path in self.files

    async def is_file(self, path: str) -> bool:
        self._record("is_file", path)
        return path in self.files

    async def is_dir(self, path: str) -> bool:
        self._record("is_dir", path)
        return False

    async def mkdir(self, path: str, **kwargs: Any) -> None:
        self._record("mkdir", path, **kwargs)

    async def remove(self, path: str, **kwargs: Any) -> None:
        self._record("remove", path, **kwargs)
        self.files.pop(path, None)

    async def list_dir(self, path: str = ".", **kwargs: Any) -> list:
        self._record("list_dir", path, **kwargs)
        return []

    async def glob(self, pattern: str, **kwargs: Any) -> list:
        self._record("glob", pattern, **kwargs)
        return []

    async def grep(self, pattern: str, **kwargs: Any) -> list:
        self._record("grep", pattern, **kwargs)
        return []

    async def exec(self, command: Any, **kwargs: Any) -> ExecResult:
        self._record("exec", command, **kwargs)
        return ExecResult(
            stdout="hello\n", stderr="", exit_code=0, wall_time_seconds=0.001
        )

    async def spawn(self, command: Any, **kwargs: Any):
        raise NotImplementedError


class _FakeProcessHandle:
    pid = 12345

    async def wait(self, timeout: float | None = None) -> ExecResult:
        return ExecResult(stdout="", stderr="", exit_code=0, wall_time_seconds=0.001)

    async def status(self) -> str:
        return "exited"

    async def terminate(self, *, force_after: float | None = None) -> None:
        return None


class _SpawningWorkspace(_RecordingWorkspace):
    async def spawn(self, command: Any, **kwargs: Any):
        self._record("spawn", command, **kwargs)
        return _FakeProcessHandle()


class _RootMismatchWorkspace(_RecordingWorkspace):
    async def read_bytes(self, path: str) -> bytes:
        self._record("read_bytes", path)
        raise WorkspaceEscapeError(f"outside workspace root: {path}")


def _invoke(tool, **params):
    """Invoke a tool against whatever workspace contextvar is currently
    installed (let the caller manage it via set_workspace)."""
    payload = {k: v for k, v in params.items() if v is not None}
    ctx = ToolContext(
        context={"workspace_root": "/fake"},
        usage=Usage(),
        tool_name=tool.name,
        tool_call_id="test-call",
        tool_arguments=payload,
    )
    return asyncio.run(tool.on_invoke_tool(ctx, json.dumps(payload)))


# -- execute_bash (foreground) -----------------------------------------


def test_execute_bash_routes_through_workspace():
    """foreground execute_bash dispatches to workspace.exec — proving the
    sandbox would receive the command the same way."""
    fake = _RecordingWorkspace()
    token = set_workspace(fake)
    try:
        result = _invoke(bash_tool.execute_bash, command="echo hello")
    finally:
        reset_workspace(token)

    names = [c[0] for c in fake.calls]
    assert "exec" in names
    exec_call = next(c for c in fake.calls if c[0] == "exec")
    assert exec_call[1][0] == "echo hello"
    assert exec_call[2]["shell"] is True
    assert result.ui_metadata["metadata"]["success"] is True
    assert result.ui_metadata["metadata"]["background"] is False


def test_log_path_for_does_not_create_host_directory(monkeypatch):
    def fail_makedirs(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("log_path_for must not create host directories")

    monkeypatch.setattr(_bash_jobs.os, "makedirs", fail_makedirs)

    assert (
        _bash_jobs.log_path_for("/workspace", "bash-test")
        == "/workspace/.omniagents/jobs/bash-test.log"
    )


def test_background_execute_bash_prepares_log_via_workspace_before_spawn(monkeypatch):
    async def noop_watch_job(job: _bash_jobs.Job) -> None:
        return None

    registry = _bash_jobs.get_registry()
    registry._jobs.clear()  # type: ignore[attr-defined]
    monkeypatch.setattr(bash_tool, "_watch_job", noop_watch_job)

    fake = _SpawningWorkspace()
    token = set_workspace(fake)
    try:
        result = _invoke(
            bash_tool.execute_bash,
            command="echo background",
            background=True,
        )
    finally:
        reset_workspace(token)
        registry._jobs.clear()  # type: ignore[attr-defined]

    metadata = result.ui_metadata["metadata"]
    assert metadata["background"] is True
    assert metadata["pid"] == 12345
    assert metadata["log_path"].startswith("/fake/.omniagents/jobs/bash-")

    names = [call[0] for call in fake.calls]
    assert names.index("exec") < names.index("spawn")

    exec_call = next(call for call in fake.calls if call[0] == "exec")
    setup_command = exec_call[1][0]
    assert (
        f"mkdir -p /fake/.omniagents/jobs && : > {metadata['log_path']}"
        == setup_command
    )
    assert exec_call[2]["shell"] is True
    assert exec_call[2]["timeout"] == 5.0

    spawn_call = next(call for call in fake.calls if call[0] == "spawn")
    assert f">> {metadata['log_path']}" in spawn_call[1][0]
    assert spawn_call[2]["cwd"] == "/fake"
    assert spawn_call[2]["shell"] is True
    assert spawn_call[2]["pty"] is False


# -- notebook tools ----------------------------------------------------


_SAMPLE_NB = json.dumps(
    {
        "cells": [
            {
                "cell_type": "code",
                "source": ["print('hi')\n"],
                "outputs": [],
                "execution_count": None,
                "metadata": {},
            }
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    },
    indent=1,
)


def test_read_notebook_routes_through_workspace():
    fake = _RecordingWorkspace(files={"/fake/nb.ipynb": _SAMPLE_NB})
    token = set_workspace(fake)
    try:
        result = _invoke(notebook_tools.read_notebook, file_path="/fake/nb.ipynb")
    finally:
        reset_workspace(token)

    names = [c[0] for c in fake.calls]
    # read_notebook calls exists() to validate then read_text() to load.
    assert "exists" in names
    assert "read_text" in names
    assert result.ui_metadata["metadata"]["success"] is True
    assert result.ui_metadata["metadata"]["cell_count"] == 1


def test_edit_notebook_routes_through_workspace():
    """edit_notebook reads via read_text and writes via write_text — both
    routed through the workspace."""
    fake = _RecordingWorkspace(files={"/fake/nb.ipynb": _SAMPLE_NB})
    token = set_workspace(fake)
    try:
        result = _invoke(
            notebook_tools.edit_notebook,
            file_path="/fake/nb.ipynb",
            cell_number=0,
            new_source="print('updated')",
            edit_mode="replace",
        )
    finally:
        reset_workspace(token)

    names = [c[0] for c in fake.calls]
    assert "read_text" in names
    assert "write_text" in names
    # write must come AFTER read.
    assert names.index("read_text") < names.index("write_text")
    assert result.ui_metadata["metadata"]["success"] is True
    # In-memory file has been updated.
    assert "print('updated')" in fake.files["/fake/nb.ipynb"]


# -- convert_to_markdown -----------------------------------------------


def test_convert_to_markdown_routes_through_workspace(monkeypatch):
    """For non-host workspaces, convert_to_markdown should read the file
    via ``workspace.read_bytes`` and bridge it to a host temp file for
    MarkItDown — the workspace contract is the only entry point to the
    file's content."""

    fake = _RecordingWorkspace(files={"/fake/doc.pdf": "fake-pdf-bytes"})

    captured_path: dict = {}

    class _MockResult:
        text_content = "# converted\n\nbody"

    def fake_convert(path):
        captured_path["path"] = path
        return _MockResult()

    monkeypatch.setattr(
        conversion_tools,
        "_converter",
        lambda: type("M", (), {"convert": staticmethod(fake_convert)}),
    )

    token = set_workspace(fake)
    try:
        result = _invoke(
            conversion_tools.convert_to_markdown, file_path="/fake/doc.pdf"
        )
    finally:
        reset_workspace(token)

    names = [c[0] for c in fake.calls]
    assert "exists" in names
    assert "read_bytes" in names
    # MarkItDown received a host-side path (the bridge), not the workspace
    # path — the bridge is what makes the host-only library work in
    # sandbox mode.
    assert captured_path["path"] != "/fake/doc.pdf"
    assert result.ui_metadata["metadata"]["success"] is True


# -- worker_tools git helpers ------------------------------------------


def test_is_git_repo_routes_through_workspace():
    """_is_git_repo runs ``git -C ... rev-parse`` via workspace.exec."""
    fake = _RecordingWorkspace()
    token = set_workspace(fake)
    try:
        result = asyncio.run(worker_isolation.is_git_repo("/fake"))
    finally:
        reset_workspace(token)

    assert result is True  # our fake returns exit_code=0
    names = [c[0] for c in fake.calls]
    assert "exec" in names
    cmd = next(c for c in fake.calls if c[0] == "exec")[1][0]
    assert "rev-parse" in cmd
    assert "/fake" in cmd


def test_is_git_dirty_routes_through_workspace():
    """_is_git_dirty runs ``git status --porcelain`` via workspace.exec.

    The fake returns non-empty stdout by default, so this also confirms
    the helper interprets non-empty porcelain output as "dirty".
    """
    fake = _RecordingWorkspace()
    token = set_workspace(fake)
    try:
        result = asyncio.run(worker_isolation.is_git_dirty("/fake"))
    finally:
        reset_workspace(token)

    assert result is True  # non-empty stdout → dirty
    names = [c[0] for c in fake.calls]
    assert "exec" in names
    cmd = next(c for c in fake.calls if c[0] == "exec")[1][0]
    assert "status --porcelain" in cmd


def test_worker_worktree_ready_checks_git_sources_not_manifest_root(monkeypatch):
    fake = _RecordingWorkspace(root="/workspace")
    monkeypatch.setattr(
        "omni_code.agent_workspace_state.get_manifest_root", lambda: "/workspace"
    )
    monkeypatch.setattr(
        "omni_code.agent_workspace_state.get_sources",
        lambda: [
            {"kind": "local-git", "mountName": "api", "path": "/host/api"},
            {"kind": "git-remote", "mountName": "web", "repoUrl": "https://example.com/web.git"},
        ],
    )

    token = set_workspace(fake)
    try:
        ready = asyncio.run(worker_tools._is_worker_worktree_ready("/workspace"))
    finally:
        reset_workspace(token)

    assert ready is True
    commands = [call[1][0] for call in fake.calls if call[0] == "exec"]
    assert any("git -C /host/api rev-parse" in c for c in commands)
    assert any("git -C /workspace/web rev-parse" in c for c in commands)
    assert not any("git -C /workspace rev-parse" in c for c in commands)


def test_create_worker_worktree_for_sources_preserves_mount_layout(monkeypatch):
    fake = _RecordingWorkspace(root="/workspace")

    async def exec_with_missing_scratch(command: Any, **kwargs: Any) -> ExecResult:
        fake._record("exec", command, **kwargs)
        if isinstance(command, str) and command.startswith("test -e "):
            return ExecResult(stdout="", stderr="", exit_code=1, wall_time_seconds=0.001)
        return ExecResult(stdout="", stderr="", exit_code=0, wall_time_seconds=0.001)

    fake.exec = exec_with_missing_scratch  # type: ignore[method-assign]

    monkeypatch.setattr(worker_tools, "_worktrees_dir", lambda: "/scratch/worktrees")
    monkeypatch.setattr(
        "omni_code.agent_workspace_state.get_manifest_root", lambda: "/workspace"
    )
    monkeypatch.setattr(
        "omni_code.agent_workspace_state.get_sources",
        lambda: [
            {"kind": "local-git", "mountName": "api", "path": "/host/api"},
            {
                "kind": "git-remote",
                "mountName": "web",
                "repoUrl": "https://example.com/web.git",
            },
            {"kind": "local", "mountName": "notes", "path": "/host/notes"},
        ],
    )

    token = set_workspace(fake)
    try:
        info = asyncio.run(
            worker_tools._create_worker_worktree_for_sources("/workspace", "pip")
        )
    finally:
        reset_workspace(token)

    assert info["worktree_path"] == "/scratch/worktrees/workspace-worker-pip"
    assert [s["mountName"] for s in info["sources"]] == ["api", "web", "notes"]
    assert info["sources"][0]["worktree_path"].endswith("/api")
    assert info["sources"][1]["source_path"] == "/workspace/web"
    assert info["sources"][1]["worktree_branch"] == "worker/workspace-worker-pip/web"
    assert info["sources"][2]["worktree_path"].endswith("/notes")

    commands = [call[1][0] for call in fake.calls if call[0] == "exec"]
    assert any("mkdir -p /scratch/worktrees/workspace-worker-pip" in c for c in commands)
    assert any("git -C /host/api worktree add" in c for c in commands)
    assert any("git -C /workspace/web worktree add" in c for c in commands)
    assert any("cp -a /host/notes" in c for c in commands)


def test_remove_worker_worktree_removes_each_source_worktree():
    from omniagents.core.workers import WorkerWorkspace

    fake = _RecordingWorkspace(root="/workspace")
    workspace = WorkerWorkspace(
        root="/scratch/worktrees/workspace-worker-pip",
        metadata={
            "worktree_path": "/scratch/worktrees/workspace-worker-pip",
            "worktree_branch": "worker/workspace-worker-pip/api",
            "worktree_name": "workspace-worker-pip",
            "parent_workspace": "/workspace",
            "sources": [
                {
                    "mountName": "api",
                    "kind": "local-git",
                    "source_path": "/host/api",
                    "worktree_path": "/scratch/worktrees/workspace-worker-pip/api",
                    "worktree_branch": "worker/workspace-worker-pip/api",
                },
                {
                    "mountName": "notes",
                    "kind": "local",
                    "source_path": "/host/notes",
                    "worktree_path": "/scratch/worktrees/workspace-worker-pip/notes",
                },
            ],
        },
    )

    token = set_workspace(fake)
    try:
        removed = asyncio.run(
            worker_tools.WorktreeIsolation().cleanup("pip", workspace)
        )
    finally:
        reset_workspace(token)

    assert removed is True
    commands = [call[1][0] for call in fake.calls if call[0] == "exec"]
    assert any("git -C /host/api worktree remove --force" in c for c in commands)
    assert any("git -C /host/api branch -D worker/workspace-worker-pip/api" in c for c in commands)
    assert any("rm -rf /scratch/worktrees/workspace-worker-pip" in c for c in commands)


# -- read_job_log ------------------------------------------------------


def test_read_job_log_routes_through_workspace():
    """read_job_log reads the log file via workspace.read_bytes.

    Bash background spawn is host-only, but the log file path lives
    inside the workspace — routing the read keeps the abstraction
    consistent and makes the read work for any backend that can see
    the path.
    """
    log_path = "/fake/.omniagents/jobs/bash-test.log"
    log_text = "line one\nline two\nline three\n"
    fake = _RecordingWorkspace()
    # Stash bytes directly under the recording fake. The fake's read_bytes
    # reads from .files (encoded), so seed that.
    fake.files[log_path] = log_text

    # Build a minimal Job-like object with just log_path + duration.
    job = type(
        "_FakeJob",
        (),
        {
            "log_path": log_path,
            "poll_returncode": lambda self: 0,
            "wall_time_ms": lambda self: 5,
        },
    )()

    token = set_workspace(fake)
    try:
        body, total, size = asyncio.run(_bash_jobs.read_job_log(job, lines=2))
    finally:
        reset_workspace(token)

    names = [c[0] for c in fake.calls]
    assert "read_bytes" in names
    # Tail of 2 lines from a 3-line file.
    assert body.splitlines() == ["line two", "line three"]
    assert total == 3
    assert size == len(log_text.encode("utf-8"))


def test_read_job_log_falls_back_to_local_registered_job_log_on_root_mismatch(tmp_path):
    job_id = "bash-local"
    log_dir = tmp_path / ".omniagents" / "jobs"
    log_dir.mkdir(parents=True)
    log_path = log_dir / f"{job_id}.log"
    log_text = "local one\nlocal two\n"
    log_path.write_text(log_text, encoding="utf-8")

    fake = _RootMismatchWorkspace(root="/other-root")
    job = type(
        "_FakeJob",
        (),
        {
            "job_id": job_id,
            "log_path": str(log_path),
            "poll_returncode": lambda self: 0,
            "wall_time_ms": lambda self: 5,
        },
    )()

    token = set_workspace(fake)
    try:
        body, total, size = asyncio.run(_bash_jobs.read_job_log(job, lines=1))
    finally:
        reset_workspace(token)

    assert [call[0] for call in fake.calls] == ["read_bytes"]
    assert body == "local two"
    assert total == 2
    assert size == len(log_text.encode("utf-8"))


def test_read_job_log_reports_missing_local_registered_job_log_after_root_mismatch(tmp_path):
    job_id = "bash-missing"
    log_path = tmp_path / ".omniagents" / "jobs" / f"{job_id}.log"
    fake = _RootMismatchWorkspace(root="/other-root")
    job = type(
        "_FakeJob",
        (),
        {
            "job_id": job_id,
            "log_path": str(log_path),
            "poll_returncode": lambda self: 0,
            "wall_time_ms": lambda self: 5,
        },
    )()

    token = set_workspace(fake)
    try:
        with pytest.raises(FileNotFoundError) as excinfo:
            asyncio.run(_bash_jobs.read_job_log(job))
    finally:
        reset_workspace(token)

    message = str(excinfo.value)
    assert "missing on local filesystem" in message
    assert "workspace read failed" in message
