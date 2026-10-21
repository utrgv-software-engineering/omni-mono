"""W1 workspace-routing tests.

Each migrated builtin tool (write_file, read_image, execute_bash) should
route all filesystem and shell I/O through ``omniagents.workspace.*``.
These tests swap the active workspace contextvar to a recording fake and
verify that the tool dispatches through it — proving that the tool will
transparently work against a SandboxWorkspace too.
"""

from __future__ import annotations

import types
from typing import Any, List, Tuple

import pytest

from omniagents import workspace as ws
from omniagents.builtin.tools.bash_tool import execute_bash
from omniagents.builtin.tools.file_tools import write_file
from omniagents.builtin.tools.image_tools import read_image
from omniagents.workspace import ExecResult, Workspace, reset_workspace, set_workspace


def unwrap(tool):
    """Return the original async function attached by the tool decorator."""
    return tool._original_func


class _RecordingWorkspace(Workspace):
    """Fake backend used to verify tools dispatch through the workspace."""

    def __init__(
        self,
        *,
        exists: bool = True,
        is_file_: bool = True,
        read_bytes_data: bytes = b"",
        exec_result: ExecResult | None = None,
    ) -> None:
        self.root = "/fake"
        self.calls: List[Tuple[str, tuple, dict]] = []
        self._exists = exists
        self._is_file = is_file_
        self._read_bytes = read_bytes_data
        self._exec_result = exec_result or ExecResult(
            stdout="fake-stdout",
            stderr="",
            exit_code=0,
            wall_time_seconds=0.001,
            truncated=False,
        )

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def resolve(self, path: str) -> str:
        self._record("resolve", path)
        if path.startswith("/"):
            return path
        return f"/fake/{path}"

    async def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        self._record("read_text", path, encoding=encoding)
        return ""

    async def read_bytes(self, path: str) -> bytes:
        self._record("read_bytes", path)
        return self._read_bytes

    async def write_text(self, path: str, content: str, **kwargs: Any) -> None:
        self._record("write_text", path, content, **kwargs)

    async def write_bytes(self, path: str, data: bytes, **kwargs: Any) -> None:
        self._record("write_bytes", path, data, **kwargs)

    async def exists(self, path: str) -> bool:
        self._record("exists", path)
        return self._exists

    async def is_file(self, path: str) -> bool:
        self._record("is_file", path)
        return self._is_file

    async def is_dir(self, path: str) -> bool:
        self._record("is_dir", path)
        return False

    async def is_exec_dir(self, path: str) -> bool:
        self._record("is_exec_dir", path)
        return True

    async def mkdir(self, path: str, **kwargs: Any) -> None:
        self._record("mkdir", path, **kwargs)

    async def remove(self, path: str, **kwargs: Any) -> None:
        self._record("remove", path, **kwargs)

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
        return self._exec_result

    async def spawn(self, command: Any, **kwargs: Any):
        self._record("spawn", command, **kwargs)
        raise NotImplementedError


@pytest.fixture
def fake_ws():
    workspace = _RecordingWorkspace()
    token = set_workspace(workspace)
    yield workspace
    reset_workspace(token)


# -- write_file -----------------------------------------------------------


@pytest.mark.asyncio
async def test_write_file_routes_through_workspace(fake_ws):
    """write_file should reach the workspace's write_text — proving the
    tool body runs entirely against the active workspace backend."""
    ctx = types.SimpleNamespace(context={"workspace_root": "/fake"})

    result = await unwrap(write_file)(ctx, "out.txt", "hello", False)

    names = [c[0] for c in fake_ws.calls]
    assert "write_text" in names
    write_call = next(c for c in fake_ws.calls if c[0] == "write_text")
    assert write_call[1][0] == "out.txt"
    assert write_call[1][1] == "hello"
    assert write_call[2]["append"] is False
    assert result.ui_metadata["metadata"]["operation"] == "write"
    assert result.ui_metadata["metadata"]["bytes_written"] == len("hello")


@pytest.mark.asyncio
async def test_write_file_append_mode_routes(fake_ws):
    ctx = types.SimpleNamespace(context={"workspace_root": "/fake"})

    await unwrap(write_file)(ctx, "log.txt", "more\n", True)

    write_call = next(c for c in fake_ws.calls if c[0] == "write_text")
    assert write_call[2]["append"] is True


# -- read_image -----------------------------------------------------------


@pytest.mark.asyncio
async def test_read_image_routes_through_workspace():
    """read_image's existence checks and byte read should all go through
    the workspace contextvar, not raw filesystem APIs."""
    png_signature = b"\x89PNG\r\n\x1a\n"  # tail not important for the test
    fake = _RecordingWorkspace(
        exists=True, is_file_=True, read_bytes_data=png_signature
    )
    token = set_workspace(fake)
    try:
        result = await unwrap(read_image)(None, "diagram.png")
    finally:
        reset_workspace(token)

    names = [c[0] for c in fake.calls]
    assert names == ["exists", "is_file", "read_bytes"]
    assert isinstance(result, list)
    assert len(result) == 2


@pytest.mark.asyncio
async def test_read_image_missing_short_circuits_via_workspace():
    fake = _RecordingWorkspace(exists=False)
    token = set_workspace(fake)
    try:
        result = await unwrap(read_image)(None, "ghost.png")
    finally:
        reset_workspace(token)

    names = [c[0] for c in fake.calls]
    assert names == ["exists"]
    assert "File not found" in result[0].text


# -- execute_bash ---------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_bash_routes_through_workspace(fake_ws, tmp_path):
    """execute_bash should call workspace.exec rather than running the
    command directly via subprocess.run."""
    ctx = types.SimpleNamespace(context={"workspace_root": str(tmp_path)})

    result = await unwrap(execute_bash)(ctx, "echo routed")

    names = [c[0] for c in fake_ws.calls]
    assert names == ["is_exec_dir", "exec"]
    exec_call = next(c for c in fake_ws.calls if c[0] == "exec")
    assert exec_call[1][0] == "echo routed"
    assert exec_call[2]["cwd"] == str(tmp_path)
    assert exec_call[2]["shell"] is True
    assert result.ui_metadata["metadata"]["stdout"] == "fake-stdout"
