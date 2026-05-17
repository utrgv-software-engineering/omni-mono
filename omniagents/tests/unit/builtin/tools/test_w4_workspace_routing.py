"""W4 workspace-routing test for download_file.

``download_file`` is the canonical host-network + sandbox-write hybrid:
the HTTP call always runs host-side via ``requests``, but the final file
write must route through ``omniagents.workspace.write_bytes`` so the
download lands inside the sandbox manifest when a SandboxWorkspace is
active.

The tempfile fallback (``output_path is None``) is documented as
host-local — it deliberately bypasses the workspace, since
``tempfile.mkstemp`` produces a path outside any workspace root.
"""

from __future__ import annotations

import io
import sys
import types
from typing import Any, List, Tuple

import pytest

if "pygit2" not in sys.modules:
    fake_pygit2 = types.ModuleType("pygit2")

    class _StubRepo:
        def __init__(self, gitdir):
            self.workdir = gitdir

        def path_is_ignored(self, path):
            return False

        def status_file(self, rel):
            return 0

    fake_pygit2.discover_repository = lambda start_dir=None: None
    fake_pygit2.Repository = _StubRepo
    fake_pygit2.GIT_STATUS_IGNORED = 0x1000
    sys.modules["pygit2"] = fake_pygit2

import importlib

download_module = importlib.import_module("omniagents.builtin.tools.download_file")
from omniagents.workspace import ExecResult, Workspace, reset_workspace, set_workspace


def unwrap(tool):
    return tool._original_func


class _RecordingWorkspace(Workspace):
    """In-memory workspace fake that records every call."""

    def __init__(self, root: str = "/fake"):
        self.root = root
        self.calls: List[Tuple[str, tuple, dict]] = []
        self.files: dict[str, bytes] = {}

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def resolve(self, path: str) -> str:
        self._record("resolve", path)
        if path.startswith(self.root):
            return path
        if path.startswith("/"):
            return path
        return f"{self.root}/{path.lstrip('/')}"

    async def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        self._record("read_text", path, encoding=encoding)
        return ""

    async def read_bytes(self, path: str) -> bytes:
        self._record("read_bytes", path)
        return b""

    async def write_text(self, path: str, content: str, **kwargs: Any) -> None:
        self._record("write_text", path, content, **kwargs)

    async def write_bytes(self, path: str, data: bytes, **kwargs: Any) -> None:
        self._record("write_bytes", path, data, **kwargs)
        self.files[path] = data

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


class DummyResponse:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, status_code=200, headers=None, content=b"", text=""):
        self.status_code = status_code
        self.headers = headers or {}
        self._content = content
        self.text = text

    @property
    def content(self):
        return self._content

    def iter_content(self, chunk_size=8192):
        stream = io.BytesIO(self._content)
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            yield chunk


@pytest.fixture
def fake_ws():
    ws = _RecordingWorkspace()
    token = set_workspace(ws)
    yield ws
    reset_workspace(token)


@pytest.mark.asyncio
async def test_download_file_with_output_path_routes_through_workspace(
    monkeypatch, fake_ws
):
    """When the caller passes an absolute output_path, download_file must
    write via ``workspace.write_bytes`` — proving the same body works
    against a SandboxWorkspace."""
    response = DummyResponse(
        status_code=200,
        headers={"Content-Type": "application/octet-stream"},
        content=b"sandbox-payload",
    )
    monkeypatch.setattr(
        download_module.requests,
        "get",
        lambda url, headers, stream, timeout: response,
    )

    result = await unwrap(download_module.download_file)(
        None, "https://example.com/file.bin", "/fake/out.bin"
    )

    names = [c[0] for c in fake_ws.calls]
    assert "write_bytes" in names
    write_call = next(c for c in fake_ws.calls if c[0] == "write_bytes")
    assert write_call[1][0] == "/fake/out.bin"
    assert write_call[1][1] == b"sandbox-payload"
    assert result.ui_metadata["metadata"]["success"] is True
    assert result.ui_metadata["metadata"]["file_size"] == len(b"sandbox-payload")


@pytest.mark.asyncio
async def test_download_file_tempfile_fallback_bypasses_workspace(monkeypatch, fake_ws):
    """The tempfile fallback (output_path=None) is documented as
    host-local — it deliberately bypasses the workspace, since the
    tempfile path lives outside any workspace root."""
    response = DummyResponse(
        status_code=200,
        headers={
            "Content-Type": "application/pdf",
            "Content-Disposition": "attachment; filename=doc.pdf",
        },
        content=b"pdfdata",
    )
    monkeypatch.setattr(
        download_module.requests,
        "get",
        lambda url, headers, stream, timeout: response,
    )

    await unwrap(download_module.download_file)(None, "https://example.com/doc")

    names = [c[0] for c in fake_ws.calls]
    assert "write_bytes" not in names
