"""HostWorkspace + contextvar routing tests.

SandboxWorkspace is exercised separately when an SDK session is available;
here we cover the host backend's full surface and verify that the
contextvar correctly routes top-level functions to whichever workspace is
active.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from omniagents import workspace as ws
from omniagents.workspace import (
    ExecResult,
    HostWorkspace,
    Workspace,
    WorkspaceEscapeError,
    WorkspaceFeatureUnavailable,
    reset_workspace,
    set_workspace,
)


@pytest.fixture
def host_ws(tmp_path: Path) -> HostWorkspace:
    return HostWorkspace(root=str(tmp_path))


@pytest.fixture
def routed(tmp_path: Path):
    """Install a HostWorkspace at tmp_path as the current workspace and
    reset on teardown so other tests aren't affected."""
    workspace = HostWorkspace(root=str(tmp_path))
    token = set_workspace(workspace)
    yield workspace
    reset_workspace(token)


# -- File I/O --------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_and_read_text(host_ws):
    await host_ws.write_text("hello.txt", "world")
    assert await host_ws.read_text("hello.txt") == "world"


@pytest.mark.asyncio
async def test_write_and_read_bytes(host_ws):
    await host_ws.write_bytes("img.bin", b"\x89PNG\r\n")
    assert await host_ws.read_bytes("img.bin") == b"\x89PNG\r\n"


@pytest.mark.asyncio
async def test_write_creates_parent_dirs(host_ws):
    await host_ws.write_text("a/b/c/leaf.txt", "deep")
    assert await host_ws.read_text("a/b/c/leaf.txt") == "deep"


@pytest.mark.asyncio
async def test_append_mode(host_ws):
    await host_ws.write_text("log.txt", "first\n")
    await host_ws.write_text("log.txt", "second\n", append=True)
    assert await host_ws.read_text("log.txt") == "first\nsecond\n"


@pytest.mark.asyncio
async def test_exists_is_file_is_dir(host_ws):
    await host_ws.write_text("file.txt", "x")
    await host_ws.mkdir("subdir")
    assert await host_ws.exists("file.txt")
    assert await host_ws.is_file("file.txt")
    assert not await host_ws.is_dir("file.txt")
    assert await host_ws.is_dir("subdir")
    assert not await host_ws.is_file("subdir")
    assert not await host_ws.exists("nope")


@pytest.mark.asyncio
async def test_remove_file_and_dir(host_ws):
    await host_ws.write_text("to_remove.txt", "x")
    await host_ws.remove("to_remove.txt")
    assert not await host_ws.exists("to_remove.txt")

    await host_ws.mkdir("dir/nested")
    await host_ws.write_text("dir/file.txt", "x")
    await host_ws.remove("dir", recursive=True)
    assert not await host_ws.exists("dir")


# -- Path safety -----------------------------------------------------------


@pytest.mark.asyncio
async def test_composite_host_exists_outside_mounts(tmp_path: Path):
    """Host file probes use the same unrestricted paths as host execution."""
    from omniagents.workspace.mounted_host import MountedHostWorkspace

    (tmp_path / "src").mkdir()
    workspace = MountedHostWorkspace(
        str(tmp_path / "logical"),
        [{"kind": "local", "mountName": "src", "path": str(tmp_path / "src")}],
    )
    external = tmp_path / "external.txt"
    external.write_text("host file")
    assert await workspace.exists(str(external)) is True
    assert await workspace.exists("../external.txt") is True
    assert await workspace.exists(str(tmp_path / "missing.txt")) is False


# -- Confinement (the "host" no-sandbox target) ---------------------------


@pytest.mark.asyncio
async def test_host_workspace_allows_out_of_root_absolute(tmp_path: Path):
    """HostWorkspace never fences: absolute paths outside root do not raise —
    the agent may reach anywhere the process can, as its shell already does."""
    unconfined = HostWorkspace(root=str(tmp_path))
    # No raise, returns the resolved absolute path verbatim.
    assert unconfined.resolve("/etc/hostname") == "/etc/hostname"
    assert unconfined.resolve("../../outside.txt").endswith("outside.txt")


@pytest.mark.asyncio
async def test_host_workspace_anchors_relative_at_root(tmp_path: Path):
    """``root`` is an anchor, not a fence: relative paths still resolve under
    it (cwd ergonomics unchanged); only the escape rejection is absent."""
    unconfined = HostWorkspace(root=str(tmp_path))
    resolved = unconfined.resolve("sub/file.txt")
    assert resolved == str((tmp_path / "sub" / "file.txt").resolve())


@pytest.mark.asyncio
async def test_host_workspace_has_no_fenced_mode(tmp_path: Path):
    """There is no "host but fenced" tier to construct.

    The knob that used to select one is gone, not defaulted: a caller cannot
    reintroduce the split where ``cat /etc/passwd`` works and
    ``read_file("/etc/passwd")`` raises. Callers needing the root boundary
    ask for it per-call via ``resolve_confined``.
    """
    import inspect

    assert "confine" not in inspect.signature(HostWorkspace.__init__).parameters
    workspace = HostWorkspace(root=str(tmp_path))
    assert workspace.enforces_confinement is False
    assert workspace.resolve("/etc/passwd") == "/etc/passwd"


# -- resolve_confined: confinement as the caller's policy -----------------
#
# The wire surfaces (fs_*, git_*) promise their paths stay inside the root
# whatever the execution target is, so they ask for the boundary explicitly
# instead of inheriting whatever the workspace happens to enforce.


@pytest.mark.asyncio
async def test_resolve_confined_reimposes_the_boundary_when_unconfined(
    tmp_path: Path,
):
    unconfined = HostWorkspace(root=str(tmp_path))

    assert unconfined.resolve("/etc/hostname") == "/etc/hostname"
    with pytest.raises(WorkspaceEscapeError):
        unconfined.resolve_confined("/etc/hostname")


@pytest.mark.asyncio
async def test_resolve_confined_catches_a_symlink_out_of_the_root(tmp_path: Path):
    """The case the wire canonicalizers structurally cannot catch.

    ``canonical_relpath`` rejects absolute and ``..`` spellings before any
    I/O, so a symlink *inside* the root is the only way a legal wire path
    can name a file outside it.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    root = tmp_path / "workspace"
    root.mkdir()
    os.symlink(outside / "secret.txt", root / "link.txt")
    unconfined = HostWorkspace(root=str(root))

    assert unconfined.resolve("link.txt") == str(outside / "secret.txt")
    with pytest.raises(WorkspaceEscapeError):
        unconfined.resolve_confined("link.txt")


@pytest.mark.asyncio
async def test_composite_host_resolve_confined_checks_mount_membership(
    tmp_path: Path,
):
    """Explicitly bounded RPC paths still enforce the composite workspace."""
    from omniagents.workspace.mounted_host import MountedHostWorkspace

    (tmp_path / "src").mkdir()
    confined = MountedHostWorkspace(
        str(tmp_path / "logical"),
        [{"kind": "local", "mountName": "src", "path": str(tmp_path / "src")}],
    )

    assert confined.enforces_confinement is False
    assert confined.resolve_confined("sub/file.txt") == str(
        (tmp_path / "logical" / "sub" / "file.txt").resolve()
    )
    assert confined.resolve_confined("src/file.txt") == str(
        tmp_path / "src" / "file.txt"
    )
    with pytest.raises(WorkspaceEscapeError):
        confined.resolve_confined(str(tmp_path / "external.txt"))


def test_current_workspace_fails_without_run_binding():
    from omniagents.workspace import (
        WorkspaceNotBoundError,
        current_workspace,
        reset_workspace,
        set_workspace,
    )

    token = set_workspace(None)
    try:
        with pytest.raises(WorkspaceNotBoundError):
            current_workspace()
    finally:
        reset_workspace(token)


# -- Navigable cwd: clamp_within + sandbox binding ------------------------


def test_clamp_within():
    from omniagents.workspace import clamp_within

    root = "/workspace/proj"
    # Root itself and descendants are preserved (navigation within boundary).
    assert clamp_within(root, root) == root
    assert clamp_within("/workspace/proj/src/app", root) == "/workspace/proj/src/app"
    # Anything outside the boundary (a host path, a sibling) clamps to root.
    assert clamp_within("/home/user/proj", root) == root
    assert clamp_within("/workspace/other", root) == root
    # Empty / missing requests default to root.
    assert clamp_within(None, root) == root
    assert clamp_within("", root) == root


def test_binding_roots_sandbox_workspace_at_navigable_cwd(tmp_path: Path):
    """WorkspaceBindingCapability.bind roots the SandboxWorkspace at the
    requested cwd when it's within the manifest root, clamps a host-path
    leak back to the manifest root, and defaults to the manifest root."""
    from omniagents.workspace import (
        current_workspace,
        set_requested_cwd,
        set_workspace,
    )
    from omniagents.workspace.binding import WorkspaceBindingCapability

    class _Manifest:
        root = "/workspace/proj"

    class _State:
        manifest = _Manifest()

    class _Session:
        state = _State()
        _inner = None

    cap = WorkspaceBindingCapability()
    token = set_workspace(None)
    try:
        set_requested_cwd("/workspace/proj/src")
        cap.bind(_Session())
        assert current_workspace().root == "/workspace/proj/src"

        set_requested_cwd("/home/user/leaked")  # host path can't root a sandbox run
        cap.bind(_Session())
        assert current_workspace().root == "/workspace/proj"

        set_requested_cwd(None)
        cap.bind(_Session())
        assert current_workspace().root == "/workspace/proj"
    finally:
        set_requested_cwd(None)
        reset_workspace(token)


# -- Discovery -------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_dir(host_ws):
    await host_ws.write_text("a.txt", "x")
    await host_ws.write_text("b.txt", "x")
    await host_ws.mkdir("subdir")
    entries = await host_ws.list_dir(".")
    names = [e.name for e in entries]
    assert "a.txt" in names
    assert "b.txt" in names
    assert "subdir" in names


@pytest.mark.asyncio
async def test_list_dir_skips_hidden_by_default(host_ws):
    await host_ws.write_text(".hidden", "x")
    await host_ws.write_text("visible.txt", "x")
    visible = [e.name for e in await host_ws.list_dir(".")]
    all_entries = [e.name for e in await host_ws.list_dir(".", include_hidden=True)]
    assert ".hidden" not in visible
    assert ".hidden" in all_entries


@pytest.mark.asyncio
async def test_glob(host_ws):
    await host_ws.write_text("a.py", "x")
    await host_ws.write_text("b.py", "x")
    await host_ws.write_text("c.txt", "x")
    await host_ws.write_text("nested/d.py", "x")

    py = await host_ws.glob("*.py")
    assert sorted(py) == ["a.py", "b.py", "nested/d.py"]


@pytest.mark.asyncio
async def test_grep(host_ws):
    await host_ws.write_text("a.txt", "hello\nworld\n")
    await host_ws.write_text("b.txt", "world\nhello\n")
    matches = await host_ws.grep("hello")
    assert {(m.path, m.line_number) for m in matches} == {
        ("a.txt", 1),
        ("b.txt", 2),
    }


# -- Shell execution -------------------------------------------------------


@pytest.mark.asyncio
async def test_exec_basic(host_ws):
    result = await host_ws.exec("echo hello")
    assert isinstance(result, ExecResult)
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello"


@pytest.mark.asyncio
async def test_exec_uses_workspace_as_default_cwd(host_ws):
    result = await host_ws.exec("pwd")
    assert result.stdout.strip() == host_ws.root


@pytest.mark.asyncio
async def test_exec_timeout_returns_timeout_message(host_ws):
    result = await host_ws.exec("sleep 5", timeout=0.2)
    assert result.exit_code == -1
    assert "timed out" in result.stdout.lower() or "timed out" in result.stderr.lower()


@pytest.mark.asyncio
async def test_spawn_and_wait(host_ws):
    handle = await host_ws.spawn("echo background")
    result = await handle.wait(timeout=5.0)
    assert result.exit_code == 0
    assert "background" in result.stdout


@pytest.mark.asyncio
async def test_spawn_pty_unsupported_host(host_ws):
    with pytest.raises(WorkspaceFeatureUnavailable):
        await host_ws.spawn("echo x", pty=True)


# -- HostWorkspace.scratch_root --------------------------------------------


def test_host_workspace_scratch_root_default(tmp_path, monkeypatch):
    """Default scratch_root is under ~/.omniagents/scratch."""
    monkeypatch.delenv("OMNIAGENTS_SCRATCH", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    ws = HostWorkspace(root=str(tmp_path))
    assert ws.scratch_root.endswith("/.omniagents/scratch")


def test_host_workspace_scratch_root_from_env(tmp_path, monkeypatch):
    """OMNIAGENTS_SCRATCH overrides the default location."""
    custom = tmp_path / "custom-scratch"
    custom.mkdir()
    monkeypatch.setenv("OMNIAGENTS_SCRATCH", str(custom))
    ws = HostWorkspace(root=str(tmp_path))
    assert ws.scratch_root == str(custom.resolve())


def test_host_workspace_scratch_root_is_outside_workspace_root(tmp_path, monkeypatch):
    """scratch_root must live outside workspace root — that's the whole
    point of the abstraction (don't pollute the user's workspace files)."""
    monkeypatch.delenv("OMNIAGENTS_SCRATCH", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    ws = HostWorkspace(root=str(project))
    # scratch_root should not be inside workspace root.
    assert not ws.scratch_root.startswith(ws.root)


# -- SandboxWorkspace.spawn(pty=False) -------------------------------------


def test_sandbox_workspace_scratch_root_default_is_writable_location():
    from omniagents.workspace.sandbox import SandboxWorkspace

    class _FakeSession:
        pass

    ws = SandboxWorkspace(session=_FakeSession())
    assert ws.scratch_root == "/tmp/omniagents/scratch"


@pytest.mark.asyncio
async def test_sandbox_workspace_spawn_without_pty():
    """SandboxWorkspace.spawn(pty=False) goes through pty_exec_start with
    tty=False — proves the abstraction is now symmetric with the host
    backend's non-PTY spawn."""
    from omniagents.workspace.sandbox import SandboxWorkspace

    captured = {}

    class _FakeUpdate:
        process_id = 42
        output = b""
        exit_code = None

    class _FakeSession:
        def supports_pty(self):
            return True

        async def pty_exec_start(
            self,
            *command,
            shell=True,
            tty=False,
            user=None,
            yield_time_s=None,
            **kwargs,
        ):
            captured["command"] = command
            captured["shell"] = shell
            captured["tty"] = tty
            return _FakeUpdate()

    ws = SandboxWorkspace(session=_FakeSession())
    handle = await ws.spawn("echo bg", pty=False)
    assert captured["tty"] is False
    assert captured["shell"] is True
    assert handle.process_id is not None


@pytest.mark.asyncio
async def test_sandbox_workspace_spawn_with_pty():
    """spawn(pty=True) still works — passes tty=True through to the
    session, for callers that explicitly want a TTY."""
    from omniagents.workspace.sandbox import SandboxWorkspace

    captured = {}

    class _FakeUpdate:
        process_id = 99
        output = b""
        exit_code = None

    class _FakeSession:
        def supports_pty(self):
            return True

        async def pty_exec_start(
            self,
            *command,
            shell=True,
            tty=False,
            user=None,
            yield_time_s=None,
            **kwargs,
        ):
            captured["tty"] = tty
            return _FakeUpdate()

    ws = SandboxWorkspace(session=_FakeSession())
    await ws.spawn("echo bg", pty=True)
    assert captured["tty"] is True


@pytest.mark.asyncio
async def test_sandbox_process_handle_wait_short_circuits_terminal_spawn_update():
    from omniagents.workspace.sandbox import SandboxWorkspace

    class _FakeUpdate:
        process_id = 42
        output = b"shell failed\n"
        exit_code = 2

    class _FakeSession:
        def supports_pty(self):
            return True

        async def pty_exec_start(self, *args, **kwargs):
            return _FakeUpdate()

        async def pty_write_stdin(self, **kwargs):
            raise AssertionError("terminal processes must not be polled")

    workspace = SandboxWorkspace(session=_FakeSession())
    handle = await workspace.spawn("exit 2", pty=False)

    result = await handle.wait(timeout=1.0)

    assert result.exit_code == 2
    assert result.stdout == "shell failed\n"
    assert await handle.status() == "failed"


@pytest.mark.asyncio
async def test_sandbox_process_handle_finalizes_when_pty_entry_vanishes():
    from omniagents.workspace.sandbox import SandboxWorkspace

    class _FakeUpdate:
        process_id = 42
        output = b"sh: illegal option\n"
        exit_code = None

    class _FakeSession:
        def supports_pty(self):
            return True

        async def pty_exec_start(self, *args, **kwargs):
            return _FakeUpdate()

        async def pty_write_stdin(self, **kwargs):
            raise ValueError("unknown pty process_id 42")

    workspace = SandboxWorkspace(session=_FakeSession())
    handle = await workspace.spawn("set -o pipefail", pty=False)

    result = await handle.wait(timeout=1.0)

    assert result.exit_code == 1
    assert result.stdout == "sh: illegal option\n"
    assert await handle.status() == "failed"


@pytest.mark.asyncio
async def test_sandbox_process_handle_preserves_transient_poll_errors():
    from omniagents.workspace.sandbox import SandboxWorkspace

    class _FakeUpdate:
        process_id = 42
        output = b""
        exit_code = None

    class _FakeSession:
        def supports_pty(self):
            return True

        async def pty_exec_start(self, *args, **kwargs):
            return _FakeUpdate()

        async def pty_write_stdin(self, **kwargs):
            raise RuntimeError("transport temporarily unavailable")

    workspace = SandboxWorkspace(session=_FakeSession())
    handle = await workspace.spawn("sleep 10", pty=False)

    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        await handle.wait(timeout=1.0)

    assert await handle.status() == "running"


@pytest.mark.asyncio
async def test_sandbox_workspace_spawn_raises_when_no_pty_support():
    """If the underlying session can't track long-running processes,
    spawn() raises a feature-unavailable error — the workspace
    abstraction surfaces the backend's limitation cleanly."""
    from omniagents.workspace.sandbox import SandboxWorkspace

    class _FakeSession:
        def supports_pty(self):
            return False

    ws = SandboxWorkspace(session=_FakeSession())
    with pytest.raises(WorkspaceFeatureUnavailable):
        await ws.spawn("echo bg", pty=False)


# -- Contextvar routing ---------------------------------------------------


@pytest.mark.asyncio
async def test_top_level_functions_route_via_contextvar(routed):
    """Top-level ws.read_text etc. dispatch through the active workspace."""
    await ws.write_text("hello.txt", "world")
    assert await ws.read_text("hello.txt") == "world"
    assert await ws.exists("hello.txt")
    assert ws.workspace_root() == routed.root


@pytest.mark.asyncio
async def test_set_reset_workspace_round_trip(tmp_path):
    """set_workspace returns a token that reset_workspace consumes; previous
    workspace is restored exactly."""
    initial_workspace = HostWorkspace(root=str(tmp_path / "initial"))
    initial_token = set_workspace(initial_workspace)

    new_workspace = HostWorkspace(root=str(tmp_path / "replacement"))
    token = set_workspace(new_workspace)
    try:
        assert ws.workspace_root() == new_workspace.root
    finally:
        reset_workspace(token)

    try:
        assert ws.current_workspace() is initial_workspace
    finally:
        reset_workspace(initial_token)


@pytest.mark.asyncio
async def test_top_level_exec_routes(routed):
    result = await ws.exec("echo routed")
    assert result.exit_code == 0
    assert "routed" in result.stdout


# -- Mock-routing test (verifies routing without a real backend) ----------


class _RecordingWorkspace(Workspace):
    """Fake backend — every call records its name. Used to prove the
    contextvar dispatches to whichever workspace is set, not just to
    HostWorkspace specifically."""

    def __init__(self):
        self.root = "/fake"
        self.calls: list[tuple[str, tuple, dict]] = []

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))

    def resolve(self, path):
        self._record("resolve", path)
        return f"/fake/{path}"

    async def read_text(self, path, *, encoding="utf-8"):
        self._record("read_text", path, encoding=encoding)
        return "fake content"

    async def read_bytes(self, path):
        self._record("read_bytes", path)
        return b"fake"

    async def write_text(self, path, content, **kwargs):
        self._record("write_text", path, content, **kwargs)

    async def write_bytes(self, path, data, **kwargs):
        self._record("write_bytes", path, data, **kwargs)

    async def exists(self, path):
        self._record("exists", path)
        return True

    async def is_file(self, path):
        self._record("is_file", path)
        return True

    async def is_dir(self, path):
        self._record("is_dir", path)
        return False

    async def mkdir(self, path, **kwargs):
        self._record("mkdir", path, **kwargs)

    async def remove(self, path, **kwargs):
        self._record("remove", path, **kwargs)

    async def list_dir(self, path=".", **kwargs):
        self._record("list_dir", path, **kwargs)
        return []

    async def glob(self, pattern, **kwargs):
        self._record("glob", pattern, **kwargs)
        return []

    async def grep(self, pattern, **kwargs):
        self._record("grep", pattern, **kwargs)
        return []

    async def exec(self, command, **kwargs):
        self._record("exec", command, **kwargs)
        return ExecResult(stdout="fake", stderr="", exit_code=0, wall_time_seconds=0.01)

    async def spawn(self, command, **kwargs):
        self._record("spawn", command, **kwargs)
        raise NotImplementedError


@pytest.mark.asyncio
async def test_top_level_dispatches_to_arbitrary_workspace():
    fake = _RecordingWorkspace()
    token = set_workspace(fake)
    try:
        content = await ws.read_text("foo")
        await ws.write_text("bar", "baz")
        result = await ws.exec("ls")
    finally:
        reset_workspace(token)

    assert content == "fake content"
    assert result.exit_code == 0
    call_names = [c[0] for c in fake.calls]
    assert call_names == ["read_text", "write_text", "exec"]


# -- Auto-bind capability -------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_binding_capability_swaps_contextvar(tmp_path):
    """When the SDK runtime calls bind(session) on WorkspaceBindingCapability,
    the workspace contextvar swaps to a SandboxWorkspace wrapping that
    session. Tool bodies that call omniagents.workspace.* then route via
    the session for the rest of the run task."""
    from omniagents.workspace.binding import WorkspaceBindingCapability
    from omniagents.workspace.sandbox import SandboxWorkspace

    class _FakeManifest:
        root = "/sandbox-root"

    class _FakeState:
        manifest = _FakeManifest()

    class _FakeSession:
        state = _FakeState()

        async def exec(self, *args, **kwargs):  # mimics BaseSandboxSession
            class _R:
                stdout = b""
                stderr = b""
                exit_code = 0

            return _R()

    cap = WorkspaceBindingCapability()
    fake_session = _FakeSession()

    initial_token = ws.set_workspace(HostWorkspace(root=str(tmp_path)))
    cap.bind(fake_session)

    try:
        active = ws.current_workspace()
        assert isinstance(active, SandboxWorkspace)
        assert active.root == "/sandbox-root"
        assert active._session is fake_session
    finally:
        ws.reset_workspace(initial_token)
