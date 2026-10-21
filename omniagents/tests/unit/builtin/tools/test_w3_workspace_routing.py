"""W3 workspace-routing tests for edit_file and apply_patch.

Both tools mutate files. After migration they must route every read AND
write through ``omniagents.workspace.*`` so the same body works against
host and sandbox backends. These tests install a recording fake workspace
that captures all calls.
"""

from __future__ import annotations

import types
from datetime import datetime
from typing import Any, List, Tuple

import pytest

from omniagents.builtin.tools.file_tools import apply_patch, edit_file
from omniagents.workspace import (
    ExecResult,
    FileEntry,
    Workspace,
    reset_workspace,
    set_workspace,
)


def unwrap(tool):
    return tool._original_func


class _RecordingWorkspace(Workspace):
    """In-memory workspace fake that records every call."""

    def __init__(self, files: dict[str, str], root: str = "/fake"):
        self.root = root
        self.files = files
        self.dirs: set[str] = set()
        self.calls: List[Tuple[str, tuple, dict]] = []

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def _abs(self, path: str) -> str:
        if path == "." or path == "":
            return self.root
        if path.startswith(self.root):
            return path
        if path.startswith("/"):
            return path
        return f"{self.root}/{path.lstrip('/')}"

    def _rel(self, abs_path: str) -> str:
        if abs_path == self.root:
            return "."
        if abs_path.startswith(self.root + "/"):
            return abs_path[len(self.root) + 1 :]
        return abs_path

    def resolve(self, path: str) -> str:
        self._record("resolve", path)
        return self._abs(path)

    async def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        self._record("read_text", path, encoding=encoding)
        rel = self._rel(self._abs(path))
        if rel in self.files:
            return self.files[rel]
        raise FileNotFoundError(path)

    async def read_bytes(self, path: str) -> bytes:
        self._record("read_bytes", path)
        return self.files.get(self._rel(self._abs(path)), "").encode("utf-8")

    async def write_text(self, path: str, content: str, **kwargs: Any) -> None:
        self._record("write_text", path, content, **kwargs)
        self.files[self._rel(self._abs(path))] = content

    async def write_bytes(self, path: str, data: bytes, **kwargs: Any) -> None:
        self._record("write_bytes", path, data, **kwargs)

    async def exists(self, path: str) -> bool:
        self._record("exists", path)
        rel = self._rel(self._abs(path))
        return rel in self.files or rel in self.dirs or rel == "."

    async def is_file(self, path: str) -> bool:
        self._record("is_file", path)
        return self._rel(self._abs(path)) in self.files

    async def is_dir(self, path: str) -> bool:
        self._record("is_dir", path)
        rel = self._rel(self._abs(path))
        return rel in self.dirs or rel == "."

    async def mkdir(self, path: str, **kwargs: Any) -> None:
        self._record("mkdir", path, **kwargs)
        self.dirs.add(self._rel(self._abs(path)))

    async def remove(self, path: str, **kwargs: Any) -> None:
        self._record("remove", path, **kwargs)
        rel = self._rel(self._abs(path))
        self.files.pop(rel, None)

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
        return ExecResult(stdout="", stderr="", exit_code=0, wall_time_seconds=0)

    async def spawn(self, command: Any, **kwargs: Any):
        raise NotImplementedError


@pytest.fixture
def fake_ws():
    ws = _RecordingWorkspace(files={"src/main.py": "print('a')\n"})
    token = set_workspace(ws)
    yield ws
    reset_workspace(token)


# -- edit_file ------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_file_routes_read_and_write(fake_ws):
    """edit_file should read AND write through the workspace, not the host
    filesystem — proving sandbox-mode routing works end-to-end."""
    ctx = types.SimpleNamespace(context={"workspace_root": "/fake"})

    result = await unwrap(edit_file)(ctx, "src/main.py", "print('a')", "print('b')")

    names = [c[0] for c in fake_ws.calls]
    assert "read_text" in names
    assert "write_text" in names
    # The write must come AFTER the read.
    assert names.index("read_text") < names.index("write_text")
    # And the fake's in-memory file should reflect the edit.
    assert fake_ws.files["src/main.py"] == "print('b')\n"
    assert result.ui_metadata["metadata"]["replacements"] == 1


@pytest.mark.asyncio
async def test_edit_file_dry_run_does_not_write(fake_ws):
    """dry_run still reads but must not call write_text."""
    ctx = types.SimpleNamespace(context={"workspace_root": "/fake"})

    result = await unwrap(edit_file)(
        ctx, "src/main.py", "print('a')", "print('b')", dry_run=True
    )

    names = [c[0] for c in fake_ws.calls]
    assert "read_text" in names
    assert "write_text" not in names
    assert "Dry run" in result.llm_output


# -- apply_patch ----------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_patch_routes_add_through_workspace(fake_ws):
    """Add File hunk should call workspace.write_text (and only that)."""
    ctx = types.SimpleNamespace(context={"workspace_root": "/fake"})

    patch = (
        "*** Begin Patch\n"
        "*** Add File: /fake/created.txt\n"
        "+hello\n"
        "*** End Patch\n"
    )

    result = await unwrap(apply_patch)(ctx, patch)

    names = [c[0] for c in fake_ws.calls]
    assert "write_text" in names
    write_call = next(c for c in fake_ws.calls if c[0] == "write_text")
    # the value content is the second positional arg
    assert write_call[1][1] == "hello\n"
    assert "Added" in result.llm_output


@pytest.mark.asyncio
async def test_apply_patch_routes_update_through_workspace(fake_ws):
    """Update File hunk should read AND write through the workspace; the
    workspace-routed read precedes the workspace-routed write."""
    fake_ws.files["src/main.py"] = "line one\nline two\n"

    ctx = types.SimpleNamespace(context={"workspace_root": "/fake"})

    patch = (
        "*** Begin Patch\n"
        "*** Update File: /fake/src/main.py\n"
        "@@\n"
        " line one\n"
        "-line two\n"
        "+line two updated\n"
        "*** End Patch\n"
    )

    result = await unwrap(apply_patch)(ctx, patch)

    names = [c[0] for c in fake_ws.calls]
    assert "read_text" in names
    assert "write_text" in names
    assert "Modified" in result.llm_output
    assert fake_ws.files["src/main.py"] == "line one\nline two updated\n"


@pytest.mark.asyncio
async def test_apply_patch_routes_delete_through_workspace(fake_ws):
    """Delete File hunk should call workspace.remove."""
    fake_ws.files["goodbye.txt"] = "bye\n"
    ctx = types.SimpleNamespace(context={"workspace_root": "/fake"})

    patch = "*** Begin Patch\n" "*** Delete File: /fake/goodbye.txt\n" "*** End Patch\n"

    result = await unwrap(apply_patch)(ctx, patch)

    names = [c[0] for c in fake_ws.calls]
    assert "remove" in names
    assert "goodbye.txt" not in fake_ws.files
    assert "Deleted" in result.llm_output


@pytest.mark.asyncio
async def test_apply_patch_routes_move_through_workspace(fake_ws):
    """A Move-to update should write the new path and remove the old via
    the workspace — proving multi-step mutations stay sandbox-friendly."""
    fake_ws.files["old.txt"] = "stay\nremove\n"
    ctx = types.SimpleNamespace(context={"workspace_root": "/fake"})

    patch = (
        "*** Begin Patch\n"
        "*** Update File: /fake/old.txt\n"
        "*** Move to: /fake/new.txt\n"
        "@@\n"
        " stay\n"
        "-remove\n"
        "+kept\n"
        "*** End Patch\n"
    )

    result = await unwrap(apply_patch)(ctx, patch)

    names = [c[0] for c in fake_ws.calls]
    assert "write_text" in names
    assert "remove" in names
    # New file written with the modified content; old file removed.
    assert fake_ws.files.get("new.txt") == "stay\nkept\n"
    assert "old.txt" not in fake_ws.files
