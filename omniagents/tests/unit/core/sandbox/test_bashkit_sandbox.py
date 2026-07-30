"""BashkitSandboxSession: the in-browser sandbox.

Two things are being protected here. That the session honours the SDK
sandbox contract (start/exec/read/write/ls/mkdir/rm, plus snapshot
lifecycle), and that SandboxWorkspace works unmodified on top of it -
because that second part is what lets agent file and shell tools run in a
browser with no special casing.
"""

import io
from pathlib import Path

import pytest
import pytest_asyncio

pytest.importorskip("bashkit", reason="bashkit is an optional backend dependency")

from omniagents.core.sandbox.bashkit_engine import BashkitSandboxSession  # noqa: E402
from omniagents.workspace import SandboxWorkspace  # noqa: E402

SALES = "region,units\nwest,120\neast,340\nwest,80\n"


@pytest.fixture
def sandbox():
    return BashkitSandboxSession(files={"/workspace/sales.csv": SALES})


# ------------------------------- lifecycle ------------------------------- #


@pytest.mark.asyncio
async def test_start_and_stop(sandbox):
    await sandbox.start()
    assert await sandbox.running() is True
    await sandbox.stop()


@pytest.mark.asyncio
async def test_starts_in_the_manifest_root(sandbox):
    # SandboxWorkspace passes relative paths straight through, so a session
    # that starts anywhere else silently resolves them in the wrong place.
    await sandbox.start()
    result = await sandbox.exec("pwd")
    assert result.stdout.decode().strip() == "/workspace"


@pytest.mark.asyncio
async def test_provisioned_files_are_present(sandbox):
    await sandbox.start()
    assert (await sandbox.read(Path("/workspace/sales.csv"))).read().decode() == SALES


# --------------------------------- exec ---------------------------------- #


@pytest.mark.asyncio
async def test_exec_runs_a_pipeline(sandbox):
    await sandbox.start()
    result = await sandbox.exec("cut -d, -f1 sales.csv | tail -n +2 | sort -u")
    assert result.stdout.decode().split() == ["east", "west"]


@pytest.mark.asyncio
async def test_exec_preserves_quoting_through_the_shell_wrapper(sandbox):
    # The base class hands _exec_internal argv like ['sh','-lc',script].
    # Joining on plain spaces loses the script's own quoting and runs the
    # wrong thing, silently and with exit code 0.
    await sandbox.start()
    result = await sandbox.exec("echo 'two  spaces preserved'")
    assert result.stdout.decode().strip() == "two  spaces preserved"


@pytest.mark.asyncio
async def test_exec_reports_exit_code_and_stderr(sandbox):
    await sandbox.start()
    assert (await sandbox.exec("exit 7")).exit_code == 7
    assert (await sandbox.exec("echo oops >&2")).stderr.decode().strip() == "oops"


@pytest.mark.asyncio
async def test_exec_returns_bytes(sandbox):
    # The SDK's ExecResult carries bytes; returning str breaks the base
    # class's own helpers, which call .decode() on it.
    await sandbox.start()
    result = await sandbox.exec("echo hi")
    assert isinstance(result.stdout, bytes)
    assert isinstance(result.stderr, bytes)


# -------------------------------- files ---------------------------------- #


@pytest.mark.asyncio
async def test_write_creates_parent_directories(sandbox):
    await sandbox.start()
    await sandbox.write(Path("/workspace/deep/nested/file.txt"), io.BytesIO(b"hi\n"))
    assert (await sandbox.read(Path("/workspace/deep/nested/file.txt"))).read() == b"hi\n"


@pytest.mark.asyncio
async def test_ls_reports_full_paths_and_kinds(sandbox):
    # Matches parse_ls_la's convention (full path, no . or ..) so callers
    # written against other backends behave the same here.
    await sandbox.start()
    await sandbox.mkdir("/workspace/subdir", parents=True)
    entries = {e.path: e for e in await sandbox.ls("/workspace")}
    assert set(entries) == {"/workspace/sales.csv", "/workspace/subdir"}
    assert entries["/workspace/sales.csv"].is_dir() is False
    assert entries["/workspace/subdir"].is_dir() is True
    assert entries["/workspace/sales.csv"].size == len(SALES)


@pytest.mark.asyncio
async def test_mkdir_and_rm(sandbox):
    await sandbox.start()
    await sandbox.mkdir("/workspace/tmp", parents=True)
    assert "/workspace/tmp" in [e.path for e in await sandbox.ls("/workspace")]
    await sandbox.rm("/workspace/tmp", recursive=True)
    assert "/workspace/tmp" not in [e.path for e in await sandbox.ls("/workspace")]


# ------------------------------- snapshots -------------------------------- #


@pytest.mark.asyncio
async def test_snapshot_and_restore(sandbox):
    await sandbox.start()
    saved = sandbox.bash.snapshot()
    await sandbox.exec("echo mutated > volatile.txt")
    assert "/workspace/volatile.txt" in [e.path for e in await sandbox.ls("/workspace")]

    sandbox.bash.restore_snapshot(saved)
    paths = [e.path for e in await sandbox.ls("/workspace")]
    assert "/workspace/volatile.txt" not in paths
    assert "/workspace/sales.csv" in paths


# --------------------- the workspace layer on top of it ------------------- #


@pytest_asyncio.fixture
async def ws(sandbox):
    await sandbox.start()
    return SandboxWorkspace(sandbox)


@pytest.mark.asyncio
async def test_workspace_reports_shell_support(ws):
    assert ws.supports_shell is True


@pytest.mark.asyncio
async def test_workspace_write_is_visible_to_the_shell(ws):
    await ws.write_text("from_api.txt", "via the API\n")
    assert (await ws.exec("cat from_api.txt")).stdout.strip() == "via the API"


@pytest.mark.asyncio
async def test_shell_write_is_visible_to_the_workspace(ws):
    await ws.exec("echo 'via the shell' > from_shell.txt")
    assert (await ws.read_text("from_shell.txt")).strip() == "via the shell"


@pytest.mark.asyncio
async def test_workspace_discovery(ws):
    await ws.exec("mkdir -p subdir")
    entries = {e.name: e.is_dir for e in await ws.list_dir(".")}
    assert entries == {"sales.csv": False, "subdir": True}
    assert await ws.glob("*.csv") == ["sales.csv"]
    assert [(m.path, m.line_number) for m in await ws.grep("east")] == [("sales.csv", 3)]
