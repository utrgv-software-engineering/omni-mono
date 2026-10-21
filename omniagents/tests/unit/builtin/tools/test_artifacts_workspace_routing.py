from __future__ import annotations

import base64
import importlib
import sys
import types
from typing import Any, List, Tuple

import pytest

from omniagents.core.tools.artifacts import (
    reset_artifact_callback,
    set_artifact_callback,
)
from omniagents.workspace import ExecResult, Workspace, reset_workspace, set_workspace

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

artifacts_module = importlib.import_module("omniagents.builtin.tools.artifacts")


def unwrap(tool):
    return tool._original_func


class _RecordingWorkspace(Workspace):
    def __init__(self, root: str = "/workspace"):
        self.root = root
        self.scratch_root = "/scratch"
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

    def relative_to_workspace(self, path: str) -> str:
        self._record("relative_to_workspace", path)
        if path.startswith(self.root):
            return path[len(self.root) :].lstrip("/")
        return path

    async def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        self._record("read_text", path, encoding=encoding)
        return self.files[path].decode(encoding)

    async def read_bytes(self, path: str) -> bytes:
        self._record("read_bytes", path)
        return self.files[path]

    async def write_text(self, path: str, content: str, **kwargs: Any) -> None:
        self._record("write_text", path, content, **kwargs)
        self.files[path] = content.encode(kwargs.get("encoding", "utf-8"))

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


class _Ctx:
    def __init__(self, workspace_root: str = "/workspace"):
        self.context = {"workspace_root": workspace_root}


@pytest.fixture(autouse=True)
def artifact_callback():
    records = []
    token = set_artifact_callback(lambda t, c, m, aid: records.append((t, c, m, aid)))
    yield records
    reset_artifact_callback(token)


@pytest.fixture
def fake_ws():
    ws = _RecordingWorkspace()
    token = set_workspace(ws)
    yield ws
    reset_workspace(token)


@pytest.mark.asyncio
async def test_display_artifact_markdown_relative_path_routes_through_workspace(
    fake_ws, artifact_callback
):
    fake_ws.files["/workspace/docs/readme.md"] = b"# Sandbox\n"

    result = await unwrap(artifacts_module.display_artifact)(
        _Ctx(), "Readme", path="docs/readme.md", mode="markdown"
    )

    assert result == {"success": True, "emitted_mode": "markdown"}
    assert artifact_callback == [("Readme", "# Sandbox\n", "markdown", None)]
    assert [call[0] for call in fake_ws.calls] == ["is_file", "read_text"]
    assert fake_ws.calls[0][1][0] == "/workspace/docs/readme.md"
    assert fake_ws.calls[1][1][0] == "/workspace/docs/readme.md"


@pytest.mark.asyncio
async def test_display_artifact_html_absolute_path_routes_through_workspace(
    fake_ws, artifact_callback
):
    fake_ws.files["/workspace/site/index.html"] = b"<h1>Hello</h1>"

    result = await unwrap(artifacts_module.display_artifact)(
        _Ctx(), "Page", path="/workspace/site/index.html", mode="html"
    )

    assert result == {"success": True, "emitted_mode": "html"}
    assert artifact_callback == [("Page", "<h1>Hello</h1>", "html", None)]
    assert [call[0] for call in fake_ws.calls] == ["is_file", "read_text"]


@pytest.mark.asyncio
async def test_display_artifact_image_path_routes_through_workspace(
    fake_ws, artifact_callback
):
    image_bytes = b"\x89PNG\r\n\x1a\n"
    fake_ws.files["/workspace/images/logo.png"] = image_bytes

    result = await unwrap(artifacts_module.display_artifact)(
        _Ctx(), "Logo", path="/workspace/images/logo.png", mode="image"
    )

    expected = f"data:image/png;base64,{base64.b64encode(image_bytes).decode()}"
    assert result == {"success": True, "emitted_mode": "image"}
    assert artifact_callback == [("Logo", expected, "image", None)]
    assert [call[0] for call in fake_ws.calls] == ["is_file", "read_bytes"]


@pytest.mark.asyncio
async def test_display_artifact_absolute_path_outside_workspace_uses_workspace_policy(
    tmp_path, fake_ws, artifact_callback
):
    host_file = tmp_path / "host.md"
    host_file.write_text("host visible\n", encoding="utf-8")

    result = await unwrap(artifacts_module.display_artifact)(
        _Ctx(), "Host", path=str(host_file), mode="markdown"
    )

    # The host file exists, but only the workspace may answer for it. (On a
    # Windows host its path is not absolute in the POSIX workspace, so it is
    # looked up beneath the root instead; either way it is not found.)
    assert result["success"] is False
    assert result["error"].startswith("File not found: ")
    assert result["error"].endswith("host.md")
    assert artifact_callback == []
    assert [call[0] for call in fake_ws.calls] == ["is_file"]


@pytest.mark.asyncio
async def test_display_artifact_office_path_returns_workspace_policy_error(
    fake_ws, artifact_callback
):
    result = await unwrap(artifacts_module.display_artifact)(
        _Ctx(), "Slides", path="slides.pptx", mode="pptx"
    )

    assert result["success"] is False
    assert (
        "cannot convert office documents through the active workspace"
        in result["error"]
    )
    assert artifact_callback == []
    assert fake_ws.calls == []


@pytest.mark.asyncio
async def test_display_artifact_content_mode_bypasses_workspace(
    fake_ws, artifact_callback
):
    result = await unwrap(artifacts_module.display_artifact)(
        _Ctx(), "Inline", content="body", mode="html", artifact_id="artifact-1"
    )

    assert result == {"success": True, "emitted_mode": "html"}
    assert artifact_callback == [("Inline", "body", "html", "artifact-1")]
    assert fake_ws.calls == []
