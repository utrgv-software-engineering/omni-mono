"""BashkitWorkspace: the ABC over a bash interpreter compiled to wasm.

The point of this backend is that files and shell are the *same* filesystem,
so the tests that matter most are the cross-checks: something written through
the file API is visible to the shell, and vice versa. The rest assert parity
with HostWorkspace, since a backend that diverges silently is worse than one
that is missing.
"""

import pytest

from omniagents.workspace import BashkitWorkspace, HostWorkspace
from omniagents.workspace.types import (
    WorkspaceEscapeError,
    WorkspaceFeatureUnavailable,
)

pytest.importorskip("bashkit", reason="bashkit is an optional backend dependency")

TREE = {
    "main.py": "import os\nprint('hello')\n",
    "notes.txt": "alpha\nbeta needle\ngamma\n",
    "pkg/util.py": "def needle():\n    return 1\n",
    "pkg/data.csv": "name,qty\nfig,3\napple,5\n",
    ".hidden.txt": "secret\n",
}


@pytest.fixture
def ws():
    """Populated through bashkit's own FS handle, so the fixture stays sync."""
    workspace = BashkitWorkspace()
    fs = workspace.bash.fs()
    for path, content in TREE.items():
        absolute = f"{workspace.root}/{path}"
        parent = absolute.rsplit("/", 1)[0]
        if not fs.exists(parent):
            fs.mkdir(parent, True)
        fs.write_file(absolute, content.encode())
    return workspace


@pytest.fixture
def host(tmp_path):
    root = tmp_path / "root"
    for path, content in TREE.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return HostWorkspace(root=str(root), scratch_root=str(tmp_path / "scratch"))


# --------------------------- one filesystem --------------------------- #


@pytest.mark.asyncio
async def test_file_api_write_is_visible_to_the_shell(ws):
    await ws.write_text("from_api.txt", "written via the workspace API\n")
    result = await ws.exec("cat from_api.txt")
    assert result.stdout.strip() == "written via the workspace API"
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_shell_write_is_visible_to_the_file_api(ws):
    await ws.exec("echo 'written via the shell' > from_shell.txt")
    assert (await ws.read_text("from_shell.txt")).strip() == "written via the shell"
    assert await ws.is_file("from_shell.txt")
    assert "from_shell.txt" in await ws.glob("*.txt")


@pytest.mark.asyncio
async def test_shell_mkdir_is_visible_to_list_dir(ws):
    await ws.exec("mkdir -p made/by/shell")
    assert await ws.is_dir("made/by/shell")
    assert "made" in [e.name for e in await ws.list_dir(".")]


# ------------------------------ the shell ------------------------------ #


@pytest.mark.asyncio
async def test_supports_shell(ws):
    assert ws.supports_shell is True


@pytest.mark.asyncio
async def test_exec_pipeline(ws):
    result = await ws.exec("cut -d, -f1 pkg/data.csv | tail -n +2 | sort")
    assert result.stdout.split() == ["apple", "fig"]


@pytest.mark.asyncio
async def test_exec_accepts_argv_list(ws):
    assert (await ws.exec(["echo", "list form"])).stdout.strip() == "list form"


@pytest.mark.asyncio
async def test_exec_reports_exit_code_and_stderr(ws):
    assert (await ws.exec("exit 3")).exit_code == 3
    assert (await ws.exec("echo oops >&2")).stderr.strip() == "oops"


@pytest.mark.asyncio
async def test_exec_env_and_cwd(ws):
    assert (await ws.exec("echo $GREETING", env={"GREETING": "hi"})).stdout.strip() == "hi"
    assert (await ws.exec("pwd", cwd="pkg")).stdout.strip().endswith("/pkg")


@pytest.mark.asyncio
async def test_exec_defaults_to_the_workspace_root(ws):
    assert (await ws.exec("pwd")).stdout.strip() == ws.root


@pytest.mark.asyncio
async def test_exec_truncates_to_max_output_bytes(ws):
    result = await ws.exec("echo abcdefghij", max_output_bytes=4)
    assert result.stdout == "abcd"
    assert result.truncated is True


@pytest.mark.asyncio
async def test_spawn_is_unavailable(ws):
    with pytest.raises(WorkspaceFeatureUnavailable):
        await ws.spawn("sleep 1")


# ------------------------- parity with the host ------------------------- #


@pytest.mark.parametrize(
    "call",
    [
        lambda w: w.read_text("main.py"),
        lambda w: w.exists("pkg/util.py"),
        lambda w: w.exists("nope.py"),
        lambda w: w.is_file("main.py"),
        lambda w: w.is_dir("pkg"),
        lambda w: w.glob("*.py"),
        lambda w: w.glob("*"),
        lambda w: w.glob("*.py", recursive=False),
        lambda w: w.read_bytes("main.py"),
    ],
    ids=[
        "read_text", "exists_true", "exists_false", "is_file", "is_dir",
        "glob_py", "glob_all", "glob_shallow", "read_bytes",
    ],
)
@pytest.mark.asyncio
async def test_matches_host_workspace(ws, host, call):
    assert await call(ws) == await call(host)


@pytest.mark.asyncio
async def test_list_dir_matches_host(ws, host):
    def shape(entries):
        return [(e.name, e.is_dir, e.size) for e in entries]

    assert shape(await ws.list_dir(".")) == shape(await host.list_dir("."))
    assert shape(await ws.list_dir(".", include_hidden=True)) == shape(
        await host.list_dir(".", include_hidden=True)
    )


@pytest.mark.asyncio
async def test_grep_matches_host(ws, host):
    def shape(matches):
        return [(m.path, m.line_number, m.line) for m in matches]

    assert shape(await ws.grep("needle")) == shape(await host.grep("needle"))
    assert shape(await ws.grep("NEEDLE", case_sensitive=False)) == shape(
        await host.grep("NEEDLE", case_sensitive=False)
    )
    assert shape(await ws.grep("needle", file_pattern="*.py")) == shape(
        await host.grep("needle", file_pattern="*.py")
    )


@pytest.mark.asyncio
async def test_append_and_remove_match_host(ws, host):
    for workspace in (ws, host):
        await workspace.write_text("log.txt", "one\n")
        await workspace.write_text("log.txt", "two\n", append=True)
        await workspace.mkdir("deep/nested")
    assert await ws.read_text("log.txt") == await host.read_text("log.txt")
    assert await ws.is_dir("deep/nested") == await host.is_dir("deep/nested")

    for workspace in (ws, host):
        await workspace.remove("deep", recursive=True)
    assert await ws.exists("deep") == await host.exists("deep")


# ------------------------------ boundaries ------------------------------ #


@pytest.mark.parametrize("path", ["/etc/passwd", "../escape.txt"])
@pytest.mark.asyncio
async def test_confines_paths_to_the_root(ws, path):
    with pytest.raises(WorkspaceEscapeError):
        await ws.read_text(path)


@pytest.mark.asyncio
async def test_confine_can_be_disabled(ws):
    loose = BashkitWorkspace(ws.bash, confine=False)
    await loose.write_text("/outside.txt", "allowed\n")
    assert (await loose.read_text("/outside.txt")).strip() == "allowed"


@pytest.mark.asyncio
async def test_rejects_bash_kwargs_when_adopting_an_instance(ws):
    with pytest.raises(TypeError):
        BashkitWorkspace(ws.bash, python=True)


# --------------------------- sandbox lifecycle -------------------------- #


@pytest.mark.asyncio
async def test_snapshot_and_restore(ws):
    saved = ws.bash.snapshot()
    await ws.exec("echo mutated > volatile.txt")
    assert await ws.exists("volatile.txt")

    ws.bash.restore_snapshot(saved)
    assert not await ws.exists("volatile.txt")
    assert await ws.exists("main.py")


@pytest.mark.asyncio
async def test_provisioning_via_files(ws):
    provisioned = BashkitWorkspace(files={"/workspace/seed.txt": "from the manifest\n"})
    assert (await provisioned.read_text("seed.txt")).strip() == "from the manifest"
    assert (await provisioned.exec("cat seed.txt")).stdout.strip() == "from the manifest"
