import re
import sys
import types

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

if "bs4" not in sys.modules:
    fake_bs4 = types.ModuleType("bs4")

    def _extract_tag(html, tag):
        match = re.search(f"<{tag}[^>]*>(.*?)</{tag}>", html, re.IGNORECASE | re.DOTALL)
        return match.group(1).strip() if match else ""

    class _FakeTag:
        def __init__(self, text):
            self.string = text

    class _FakeSoup:
        def __init__(self, html, parser=None):
            self._html = html
            title = _extract_tag(html, "title")
            self.title = _FakeTag(title) if title else None

        def __call__(self, names):
            return []

        def find_all(self, name, **kwargs):
            return []

        def get_text(self, separator="\n", strip=False):
            text = re.sub(r"<[^>]+>", " ", self._html)
            if strip:
                text = text.strip()
            words = text.split()
            if separator != " ":
                return separator.join(words)
            return " ".join(words)

    fake_bs4.BeautifulSoup = _FakeSoup
    sys.modules["bs4"] = fake_bs4

import omniagents.builtin.tools.bash_tool as bash_tool
from omniagents.core.tools.output_budget import bound_output
from omniagents.workspace import ExecResult


def unwrap(tool):
    """Return the async function the tool wraps."""
    return tool._original_func


def make_ctx(tmp_path):
    return types.SimpleNamespace(context={"workspace_root": str(tmp_path)})


def make_fake_exec(stdout="", stderr="", exit_code=0, *, captured=None):
    """Build an async monkeypatch target for ``omniagents.workspace.exec``."""

    async def _fake(
        command,
        *,
        cwd=None,
        timeout=None,
        env=None,
        shell=True,
        user=None,
        max_output_bytes=None,
    ):
        if captured is not None:
            captured.update(
                {
                    "command": command,
                    "cwd": cwd,
                    "timeout": timeout,
                    "shell": shell,
                }
            )
        return ExecResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            wall_time_seconds=0.001,
            truncated=False,
        )

    return _fake


@pytest.mark.asyncio
async def test_execute_bash_success(monkeypatch, tmp_path):
    ctx = make_ctx(tmp_path)
    captured = {}

    monkeypatch.setattr(
        "omniagents.workspace.exec",
        make_fake_exec(stdout="ok\n", exit_code=0, captured=captured),
    )

    result = await unwrap(bash_tool.execute_bash)(ctx, "echo ok")

    assert captured["command"] == "echo ok"
    assert captured["cwd"] == str(tmp_path)
    assert captured["shell"] is True
    assert captured["timeout"] == 30
    assert "Exit code: 0 (success)" in result.llm_output
    assert "ok" in result.llm_output
    assert result.ui_metadata["metadata"]["stdout"] == "ok\n"
    assert result.ui_metadata["metadata"]["success"] is True
    assert "wall_time_ms" in result.ui_metadata["metadata"]


@pytest.mark.asyncio
async def test_execute_bash_failure(monkeypatch, tmp_path):
    ctx = make_ctx(tmp_path)

    monkeypatch.setattr(
        "omniagents.workspace.exec",
        make_fake_exec(stdout="out", stderr="err", exit_code=2),
    )

    result = await unwrap(bash_tool.execute_bash)(ctx, "bad command")

    assert "Exit code: 2 (failed)" in result.llm_output
    assert result.ui_metadata["metadata"]["success"] is False
    assert result.ui_metadata["metadata"]["has_stderr"] is True


@pytest.mark.asyncio
async def test_execute_bash_timeout(monkeypatch, tmp_path):
    ctx = make_ctx(tmp_path)

    # HostWorkspace.exec encodes timeouts as exit_code=-1 with a "timed out"
    # message embedded in stdout. The tool reads that shape and surfaces a
    # structured timeout error.
    monkeypatch.setattr(
        "omniagents.workspace.exec",
        make_fake_exec(
            stdout="\nCommand timed out after 1.000s.",
            stderr="",
            exit_code=-1,
        ),
    )

    result = await unwrap(bash_tool.execute_bash)(ctx, "sleep 5", timeout=1)

    assert "Command timed out" in result.llm_output
    assert result.ui_metadata["metadata"]["error_type"] == "timeout"
    assert "wall_time_ms" in result.ui_metadata["metadata"]


@pytest.mark.asyncio
async def test_execute_bash_unexpected_error(monkeypatch, tmp_path):
    ctx = make_ctx(tmp_path)

    async def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("omniagents.workspace.exec", _boom)

    result = await unwrap(bash_tool.execute_bash)(ctx, "explode")

    assert result.llm_output == "Error executing command: boom"
    assert result.ui_metadata["metadata"]["error_type"] == "execution_error"


# --- Truncation and output handling ---


@pytest.mark.asyncio
async def test_execute_bash_truncates_large_output(monkeypatch, tmp_path):
    """Test that large output is truncated with head/tail preservation."""
    ctx = make_ctx(tmp_path)

    large_output = "\n".join(["A" * 50] * 20 + ["B" * 50] * 20 + ["C" * 50] * 20)
    monkeypatch.setattr(
        "omniagents.workspace.exec",
        make_fake_exec(stdout=large_output, exit_code=0),
    )

    result = await unwrap(bash_tool.execute_bash)(
        ctx, "big output", max_output_chars=500
    )

    result = await bound_output(result, ctx)
    assert "Output shortened" in result.llm_output
    assert "AAA" in result.llm_output
    assert "CCC" in result.llm_output
    assert result.ui_metadata["output_retention"]["status"] == "unavailable"
    assert len(result.llm_output.encode()) <= 512


@pytest.mark.asyncio
async def test_execute_bash_preserves_long_lines(monkeypatch, tmp_path):
    """Long lines that fit the total budget remain intact."""
    ctx = make_ctx(tmp_path)

    long_line = "x" * 600
    monkeypatch.setattr(
        "omniagents.workspace.exec",
        make_fake_exec(stdout=long_line, exit_code=0),
    )

    result = await unwrap(bash_tool.execute_bash)(ctx, "long line")

    assert long_line in result.llm_output
    assert result.ui_metadata["metadata"]["lines_truncated"] == 0


@pytest.mark.asyncio
async def test_execute_bash_reports_wall_time(monkeypatch, tmp_path):
    """Test that wall time is reported in output."""
    ctx = make_ctx(tmp_path)

    monkeypatch.setattr(
        "omniagents.workspace.exec",
        make_fake_exec(stdout="done", exit_code=0),
    )

    result = await unwrap(bash_tool.execute_bash)(ctx, "fast command")

    assert "Wall time:" in result.llm_output
    assert "ms" in result.llm_output
    assert result.ui_metadata["metadata"]["wall_time_ms"] >= 0


@pytest.mark.asyncio
async def test_execute_bash_no_output(monkeypatch, tmp_path):
    """Test handling of commands with no output."""
    ctx = make_ctx(tmp_path)

    monkeypatch.setattr(
        "omniagents.workspace.exec",
        make_fake_exec(stdout="", stderr="", exit_code=0),
    )

    result = await unwrap(bash_tool.execute_bash)(ctx, "silent")

    assert "(no output)" in result.llm_output
    assert "Exit code: 0 (success)" in result.llm_output


@pytest.mark.asyncio
async def test_execute_bash_truncation_notice_with_stats(monkeypatch, tmp_path):
    """Test that truncation notices include helpful stats."""
    ctx = make_ctx(tmp_path)

    output = "\n".join([f"line {i}" for i in range(100)])
    monkeypatch.setattr(
        "omniagents.workspace.exec",
        make_fake_exec(stdout=output, exit_code=0),
    )

    result = await unwrap(bash_tool.execute_bash)(
        ctx, "many lines", max_output_chars=200
    )

    result = await bound_output(result, ctx)
    assert "Output shortened" in result.llm_output
    assert result.ui_metadata["output_retention"]["original_bytes"] > 512
    assert result.ui_metadata["metadata"]["total_lines"] == 100


def test_truncate_output_head_tail_preserves_boundaries():
    """Test the head/tail truncation helper function directly."""
    text = "AAAA" + "BBBB" * 100 + "CCCC"

    truncated, chars_removed, was_truncated = bash_tool._truncate_output_head_tail(
        text, 20
    )

    assert was_truncated is True
    assert chars_removed > 0
    assert truncated.startswith("AA")
    assert truncated.endswith("CC")
    assert "characters truncated" in truncated


def test_truncate_output_head_tail_no_truncation_needed():
    """Test that small output is not truncated."""
    text = "short output"

    truncated, chars_removed, was_truncated = bash_tool._truncate_output_head_tail(
        text, 1000
    )

    assert was_truncated is False
    assert chars_removed == 0
    assert truncated == text


# ---------------------------------------------------------------------------
# Strict workspace_root behavior — execute_bash refuses to inherit the
# host process's cwd silently.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_bash_raises_when_workspace_root_missing():
    """No workspace_root + no absolute cwd argument → raise rather than
    let the subprocess inherit the parent process cwd."""
    from omniagents.core.errors import WorkspaceRootRequiredError

    ctx = types.SimpleNamespace(context={})
    with pytest.raises(WorkspaceRootRequiredError):
        await unwrap(bash_tool.execute_bash)(ctx, "echo ok")


@pytest.mark.asyncio
async def test_execute_bash_absolute_cwd_does_not_require_workspace_root(
    monkeypatch, tmp_path
):
    """An absolute cwd is unambiguous; no workspace_root needed."""
    captured = {}

    monkeypatch.setattr(
        "omniagents.workspace.exec",
        make_fake_exec(stdout="ok\n", exit_code=0, captured=captured),
    )

    ctx = types.SimpleNamespace(context={})
    await unwrap(bash_tool.execute_bash)(ctx, "echo ok", cwd=str(tmp_path))

    assert captured["cwd"] == str(tmp_path)


@pytest.mark.asyncio
async def test_execute_bash_relative_cwd_anchored_to_workspace_root(
    monkeypatch, tmp_path
):
    """A relative cwd is joined onto workspace_root, never to parent cwd."""
    import os as _os

    sub = tmp_path / "subdir"
    sub.mkdir()

    captured = {}

    monkeypatch.setattr(
        "omniagents.workspace.exec",
        make_fake_exec(stdout="", exit_code=0, captured=captured),
    )

    ctx = types.SimpleNamespace(context={"workspace_root": str(tmp_path)})
    await unwrap(bash_tool.execute_bash)(ctx, "ls", cwd="subdir")

    assert captured["cwd"] == _os.path.abspath(str(sub))


@pytest.mark.asyncio
@pytest.mark.parametrize("background", [False, True])
@pytest.mark.parametrize("bad_cwd", ["null", '"/workspace/project"', "missing", "file"])
async def test_invalid_cwd_is_actionable_without_launch(
    tmp_path, monkeypatch, background, bad_cwd
):
    from unittest.mock import AsyncMock

    (tmp_path / "file").write_text("not a directory")
    execute = AsyncMock()
    spawn = AsyncMock()
    monkeypatch.setattr("omniagents.workspace.exec", execute)
    monkeypatch.setattr("omniagents.workspace.spawn", spawn)
    result = await unwrap(bash_tool.execute_bash)(
        make_ctx(tmp_path), "echo hello", cwd=bad_cwd, background=background
    )
    metadata = result.ui_metadata
    assert metadata["metadata"]["error_type"] == "invalid_working_directory"
    assert str(tmp_path) in metadata["value"]
    assert "JSON null" in metadata["value"]
    if bad_cwd.startswith('"'):
        assert "remove the surrounding quote characters" in metadata["value"]
    execute.assert_not_awaited()
    spawn.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cwd", [None, "null", "with spaces", '"quoted"'])
async def test_valid_cwd_is_preserved(tmp_path, monkeypatch, cwd):
    if cwd is not None:
        (tmp_path / cwd).mkdir()
    captured = {}
    monkeypatch.setattr("omniagents.workspace.exec", make_fake_exec(captured=captured))
    await unwrap(bash_tool.execute_bash)(make_ctx(tmp_path), "echo hello", cwd=cwd)
    assert captured["cwd"] == str(tmp_path if cwd is None else tmp_path / cwd)


@pytest.mark.asyncio
async def test_cwd_validation_uses_active_backend(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    remote_path = "/remote/only/project"
    check = AsyncMock(return_value=True)
    from omniagents import workspace

    monkeypatch.setattr(workspace.current_workspace(), "is_exec_dir", check)
    captured = {}
    monkeypatch.setattr("omniagents.workspace.exec", make_fake_exec(captured=captured))
    await unwrap(bash_tool.execute_bash)(
        make_ctx(tmp_path), "echo hello", cwd=remote_path
    )
    check.assert_awaited_once_with(remote_path)
    assert captured["cwd"] == remote_path


@pytest.mark.asyncio
async def test_sandbox_exec_directory_check_allows_outside_file_root():
    from unittest.mock import AsyncMock
    from omniagents.workspace.sandbox import SandboxWorkspace

    session = types.SimpleNamespace(
        exec=AsyncMock(return_value=types.SimpleNamespace(exit_code=0))
    )
    workspace = SandboxWorkspace(session, root="/workspace", default_user="agent")
    assert await workspace.is_exec_dir("/tmp/build with spaces")
    session.exec.assert_awaited_once_with(
        "sh", "-lc", "test -d '/tmp/build with spaces'", shell=False, user="agent"
    )
    session.exec.return_value.exit_code = 1
    assert not await workspace.is_exec_dir("/tmp/missing")
