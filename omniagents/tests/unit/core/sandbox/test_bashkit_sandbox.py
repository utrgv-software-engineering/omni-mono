"""BashkitSandboxSession: the in-browser sandbox.

Two things are being protected here. That the session honours the SDK
sandbox contract (start/exec/read/write/ls/mkdir/rm, plus snapshot
lifecycle), and that SandboxWorkspace works unmodified on top of it -
because that second part is what lets agent file and shell tools run in a
browser with no special casing.
"""

import importlib.util
import io
import os
import tarfile
from pathlib import Path

import pytest
import pytest_asyncio

REQUIRE_BASHKIT_ENV = "OMNIAGENTS_REQUIRE_BASHKIT"

if importlib.util.find_spec("bashkit") is None:
    if os.environ.get(REQUIRE_BASHKIT_ENV, "").strip() not in ("", "0"):
        raise RuntimeError(
            f"{REQUIRE_BASHKIT_ENV} is set, so this suite must execute, but "
            "bashkit is not installed. Install the sandbox-bashkit extra or "
            f"unset {REQUIRE_BASHKIT_ENV} to allow the local skip."
        )
    pytest.skip(
        "bashkit is an optional backend dependency",
        allow_module_level=True,
    )

from omniagents.core.sandbox.bashkit_engine import BashkitSandboxSession  # noqa: E402
from omniagents.workspace import SandboxWorkspace  # noqa: E402

SALES = "region,units\nwest,120\neast,340\nwest,80\n"


def _tar_with_file(name: str, contents: bytes = b"escaped\n") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo(name=name)
        info.size = len(contents)
        archive.addfile(info, io.BytesIO(contents))
    return buffer.getvalue()


@pytest.fixture
def sandbox(tmp_path):
    # The workspace is a real directory now, mounted into the shell, so tests
    # need a writable root - "/workspace" is not creatable off Pyodide.
    return BashkitSandboxSession(
        root=str(tmp_path / "workspace"), files={"sales.csv": SALES}
    )


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
    assert result.stdout.decode().strip() == sandbox.state.manifest.root


@pytest.mark.asyncio
async def test_provisioned_files_are_present(sandbox):
    await sandbox.start()
    assert (
        await sandbox.read(Path(sandbox.state.manifest.root) / "sales.csv")
    ).read().decode() == SALES


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
    await sandbox.write(
        Path(sandbox.state.manifest.root, "deep/nested/file.txt"), io.BytesIO(b"hi\n")
    )
    assert (
        await sandbox.read(Path(sandbox.state.manifest.root, "deep/nested/file.txt"))
    ).read() == b"hi\n"


@pytest.mark.asyncio
async def test_ls_reports_full_paths_and_kinds(sandbox):
    # Matches parse_ls_la's convention (full path, no . or ..) so callers
    # written against other backends behave the same here.
    await sandbox.start()
    await sandbox.mkdir(f"{sandbox.state.manifest.root}/subdir", parents=True)
    entries = {e.path: e for e in await sandbox.ls(sandbox.state.manifest.root)}
    assert set(entries) == {
        f"{sandbox.state.manifest.root}/sales.csv",
        f"{sandbox.state.manifest.root}/subdir",
    }
    assert entries[f"{sandbox.state.manifest.root}/sales.csv"].is_dir() is False
    assert entries[f"{sandbox.state.manifest.root}/subdir"].is_dir() is True
    assert entries[f"{sandbox.state.manifest.root}/sales.csv"].size == len(SALES)


@pytest.mark.asyncio
async def test_mkdir_and_rm(sandbox):
    await sandbox.start()
    await sandbox.mkdir(f"{sandbox.state.manifest.root}/tmp", parents=True)
    assert f"{sandbox.state.manifest.root}/tmp" in [
        e.path for e in await sandbox.ls(sandbox.state.manifest.root)
    ]
    await sandbox.rm(f"{sandbox.state.manifest.root}/tmp", recursive=True)
    assert f"{sandbox.state.manifest.root}/tmp" not in [
        e.path for e in await sandbox.ls(sandbox.state.manifest.root)
    ]


# ------------------------------- snapshots -------------------------------- #


@pytest.mark.asyncio
async def test_snapshot_and_restore(sandbox):
    # Files live on the real filesystem now, so the workspace is snapshotted
    # here rather than by bash.snapshot(), which only holds shell state.
    await sandbox.start()
    saved = sandbox.snapshot_workspace()
    await sandbox.exec("echo mutated > volatile.txt")
    assert f"{sandbox.state.manifest.root}/volatile.txt" in [
        e.path for e in await sandbox.ls(sandbox.state.manifest.root)
    ]

    sandbox.restore_workspace(saved)
    paths = [e.path for e in await sandbox.ls(sandbox.state.manifest.root)]
    assert f"{sandbox.state.manifest.root}/volatile.txt" not in paths
    assert f"{sandbox.state.manifest.root}/sales.csv" in paths


def test_restore_rejects_parent_traversal_without_destroying_workspace(
    sandbox, tmp_path
):
    escaped = tmp_path / "escaped.txt"

    with pytest.raises(tarfile.OutsideDestinationError):
        sandbox.restore_workspace(_tar_with_file("../escaped.txt"))

    assert not escaped.exists()
    assert Path(sandbox.state.manifest.root, "sales.csv").read_text() == SALES


def test_restore_confines_absolute_archive_paths(sandbox, tmp_path):
    escaped = tmp_path / "absolute-escape.txt"

    sandbox.restore_workspace(_tar_with_file(str(escaped)))

    assert not escaped.exists()
    # tarfile's data filter strips the leading slash, keeping the member under
    # the extraction root rather than writing to its archive-supplied path.
    confined = Path(sandbox.state.manifest.root, *escaped.parts[1:])
    assert confined.read_bytes() == b"escaped\n"


def test_restore_supports_a_relative_workspace_root(sandbox, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("relative-workspace").mkdir()
    Path("relative-workspace/original.txt").write_text("before\n")
    relative = BashkitSandboxSession(root="relative-workspace", bash=sandbox.bash)
    saved = relative.snapshot_workspace()
    Path("relative-workspace/original.txt").write_text("after\n")

    relative.restore_workspace(saved)

    assert Path("relative-workspace/original.txt").read_text() == "before\n"


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
    assert [(m.path, m.line_number) for m in await ws.grep("east")] == [
        ("sales.csv", 3)
    ]


@pytest.mark.asyncio
async def test_workspace_discovery_recurses_and_accepts_reserved_context(ws):
    await ws.write_text("deep/nested/data.csv", "before\neast\nafter\n")

    assert await ws.glob("*.csv") == ["deep/nested/data.csv", "sales.csv"]
    assert [
        (match.path, match.line_number, match.line)
        for match in await ws.grep("east", file_pattern="*.csv", context_lines=1)
    ] == [
        ("sales.csv", 3, "east,340"),
        ("deep/nested/data.csv", 2, "east"),
    ]


@pytest.mark.asyncio
async def test_mounted_discovery_refuses_paths_outside_workspace(sandbox, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("do not expose me\n")

    found = await sandbox.exec("find", str(tmp_path), "-name", "*.txt", shell=False)
    searched = await sandbox.exec(
        "grep", "-rn", "-e", "expose", str(tmp_path), shell=False
    )

    assert found.exit_code != 0
    assert str(secret).encode() not in found.stdout
    assert searched.exit_code != 0
    assert b"do not expose me" not in searched.stdout
