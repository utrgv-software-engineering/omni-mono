"""W2 workspace-routing tests.

Each migrated inspection tool (read_file, list_directory, glob_files,
grep_files) should route filesystem reads through ``omniagents.workspace.*``.
These tests install a recording fake workspace and verify that the tools
dispatch through it.
"""

from __future__ import annotations

import types
from datetime import datetime
from typing import Any, List, Tuple

import pytest

from omniagents.builtin.tools.file_tools import (
    glob_files,
    grep_files,
    list_directory,
    read_file,
)
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
    """Fake workspace backed by an in-memory file tree.

    ``files``: maps relative paths to text content (regular files).
    ``dirs``:  set of directory relative paths.
    """

    def __init__(self, files: dict[str, str], dirs: list[str], root: str = "/fake"):
        self.root = root
        self.files = files
        self.dirs = set(dirs)
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
        rel = self._rel(self._abs(path))
        if rel in self.files:
            return self.files[rel].encode("utf-8")
        raise FileNotFoundError(path)

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

    async def list_dir(self, path: str = ".", **kwargs: Any) -> list:
        self._record("list_dir", path, **kwargs)
        rel = self._rel(self._abs(path))
        prefix = "" if rel == "." else rel + "/"

        out = []
        for f in self.files:
            if f.startswith(prefix):
                tail = f[len(prefix) :]
                if "/" not in tail:
                    out.append(
                        FileEntry(
                            name=tail,
                            path=f,
                            is_dir=False,
                            size=len(self.files[f]),
                            mtime=datetime.fromtimestamp(0),
                            is_symlink=False,
                        )
                    )
        for d in self.dirs:
            if d.startswith(prefix) and d != prefix.rstrip("/"):
                tail = d[len(prefix) :]
                if "/" not in tail:
                    out.append(
                        FileEntry(
                            name=tail,
                            path=d,
                            is_dir=True,
                            size=None,
                            mtime=datetime.fromtimestamp(0),
                            is_symlink=False,
                        )
                    )
        out.sort(key=lambda e: e.name)
        return out

    async def glob(self, pattern: str, **kwargs: Any) -> list:
        self._record("glob", pattern, **kwargs)
        return []

    async def grep(self, pattern: str, **kwargs: Any) -> list:
        self._record("grep", pattern, **kwargs)
        return []

    async def exec(self, command: Any, **kwargs: Any) -> ExecResult:
        self._record("exec", command, **kwargs)
        for prefix, out in getattr(self, "exec_outputs", {}).items():
            if str(command).startswith(prefix):
                return ExecResult(
                    stdout=out, stderr="", exit_code=0, wall_time_seconds=0
                )
        return ExecResult(stdout="", stderr="", exit_code=0, wall_time_seconds=0)

    async def spawn(self, command: Any, **kwargs: Any):
        raise NotImplementedError


@pytest.fixture
def fake_ws():
    ws = _RecordingWorkspace(
        files={
            "src/main.py": "import sys\nprint('hello')\n",
            "README.md": "# title\nabc\n",
        },
        dirs=["src"],
    )
    token = set_workspace(ws)
    yield ws
    reset_workspace(token)


# -- read_file ------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_file_routes_through_workspace(fake_ws):
    ctx = types.SimpleNamespace(context={"workspace_root": "/fake"})
    result = await unwrap(read_file)(ctx, "src/main.py")

    names = [c[0] for c in fake_ws.calls]
    assert "read_text" in names
    assert "L1: import sys" in result.llm_output


# -- glob_files -----------------------------------------------------------


@pytest.mark.asyncio
async def test_glob_files_routes_through_workspace(fake_ws, monkeypatch):
    # Force the walk path — the ripgrep fast path is exercised separately;
    # here we pin that the fallback stays workspace-routed.
    import omniagents.builtin.tools.file_tools as ft

    async def _no_rg():
        return False

    monkeypatch.setattr(ft, "_rg_available", _no_rg)

    ctx = types.SimpleNamespace(context={"workspace_root": "/fake"})
    result = await unwrap(glob_files)(ctx, "**/*.py")

    names = [c[0] for c in fake_ws.calls]
    assert "list_dir" in names
    paths = result.ui_metadata["value"]
    assert any(p.endswith("main.py") for p in paths)


# -- grep_files -----------------------------------------------------------


@pytest.mark.asyncio
async def test_grep_files_routes_through_workspace(fake_ws, monkeypatch):
    # Force the walk path — the ripgrep fast path is exercised separately;
    # here we pin that the fallback stays workspace-routed.
    import omniagents.builtin.tools.file_tools as ft

    async def _no_rg():
        return False

    monkeypatch.setattr(ft, "_rg_available", _no_rg)

    ctx = types.SimpleNamespace(context={"workspace_root": "/fake"})
    result = await unwrap(grep_files)(ctx, "hello")

    names = [c[0] for c in fake_ws.calls]
    # grep_files walks the tree (list_dir) and reads candidate files
    # (read_text) — both should be routed through the workspace.
    assert "list_dir" in names
    assert "read_text" in names
    files_with_match = [m["file"] for m in result.ui_metadata["value"]]
    assert any(f.endswith("main.py") for f in files_with_match)


@pytest.mark.asyncio
async def test_glob_and_grep_fast_paths_route_through_workspace_exec(fake_ws):
    """With ripgrep available, discovery goes through workspace.exec —
    a single call instead of a per-directory/per-file walk."""
    import omniagents.builtin.tools.file_tools as ft

    ft._RG_PROBE_CACHE.clear()
    fake_ws.exec_outputs = {
        "rg --version": "ripgrep 14.0.0\n",
        "rg --files": "/fake/src/main.py\n",
        "rg -c": "/fake/src/main.py:2\n",
    }

    ctx = types.SimpleNamespace(context={"workspace_root": "/fake"})
    result = await unwrap(glob_files)(ctx, "**/*.py")
    exec_cmds = [c[1][0] for c in fake_ws.calls if c[0] == "exec"]
    assert any("rg --files" in c for c in exec_cmds)
    assert "list_dir" not in [c[0] for c in fake_ws.calls]
    assert any(p.endswith("main.py") for p in result.ui_metadata["value"])

    fake_ws.calls.clear()
    ft._RG_PROBE_CACHE.clear()
    result = await unwrap(grep_files)(ctx, "hello")
    exec_cmds = [c[1][0] for c in fake_ws.calls if c[0] == "exec"]
    assert any(c.startswith("rg -c") for c in exec_cmds)
    assert "read_text" not in [c[0] for c in fake_ws.calls]
    matches = result.ui_metadata["value"]
    assert matches and matches[0]["file"] == "/fake/src/main.py"
    assert matches[0]["match_count"] == 2


# -- list_directory -------------------------------------------------------


@pytest.mark.asyncio
async def test_list_directory_routes_through_workspace(fake_ws):
    ctx = types.SimpleNamespace(context={"workspace_root": "/fake"})
    result = await unwrap(list_directory)(ctx, "/fake")

    names = [c[0] for c in fake_ws.calls]
    assert "is_dir" in names
    assert "list_dir" in names
    assert result.ui_metadata["metadata"]["total_entries"] >= 1
