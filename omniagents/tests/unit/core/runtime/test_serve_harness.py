"""Tests for the serve-session harness (``omniagents.core.runtime.serve_harness``).

Covers the moved core with fakes — no docker daemon required:

* snapshot scrub on a crafted tar containing symlink escapes.
* teardown idempotence / ``None``-safety / snapshot discard.
"""

from __future__ import annotations

import asyncio
import io
import tarfile
from pathlib import Path
from types import SimpleNamespace
import pytest

from omniagents.core.runtime.serve_harness import (
    _register_execution_environment,
    _resolve_service_urls,
    _safe_symlink_target,
    _scrub_workspace_snapshot,
    teardown_session,
)

# --------------------------------------------------------------------------
# Snapshot scrub
# --------------------------------------------------------------------------


def test_safe_symlink_target_predicate():
    assert _safe_symlink_target("a/.fvm/flutter_sdk", "/Users/x/fvm/3.32.8") is False
    assert _safe_symlink_target("a/b/link", "../../../etc/passwd") is False
    assert _safe_symlink_target("a/b/link", "../b/file") is True
    assert _safe_symlink_target("a/link", "file") is True


def _add_file(tar, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _add_symlink(tar, name, target):
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    tar.addfile(info)


def test_scrub_drops_unsafe_symlinks_keeps_the_rest(tmp_path):
    snap = tmp_path / "sess.tar"
    with tarfile.open(snap, "w") as t:
        _add_file(t, "repo/README.md", b"hi")
        _add_symlink(t, "repo/.fvm/flutter_sdk", "/Users/someone/fvm/3.32.8")
        _add_symlink(t, "repo/escape", "../../../etc/passwd")
        _add_symlink(t, "repo/ok", "README.md")

    session = SimpleNamespace(
        state=SimpleNamespace(
            snapshot=SimpleNamespace(base_path=str(tmp_path), id="sess")
        )
    )
    _scrub_workspace_snapshot(session)

    with tarfile.open(snap, "r") as t:
        names = {m.name for m in t.getmembers()}
    assert names == {"repo/README.md", "repo/ok"}


def test_scrub_noops_on_noop_snapshot_and_missing_tar(tmp_path):
    # NoopSnapshot-like shape (no base_path/id) → silently skipped.
    _scrub_workspace_snapshot(SimpleNamespace(state=SimpleNamespace(snapshot=object())))
    # Configured snapshot whose tar was never written → silently skipped.
    session = SimpleNamespace(
        state=SimpleNamespace(
            snapshot=SimpleNamespace(base_path=str(tmp_path), id="never-persisted")
        )
    )
    _scrub_workspace_snapshot(session)


# --------------------------------------------------------------------------
# Service URL resolution
# --------------------------------------------------------------------------


class _PortSession:
    async def resolve_exposed_port(self, port):
        return SimpleNamespace(host="127.0.0.1", port=port)


def test_resolve_service_urls_applies_default_suffixes():
    from omniagents.core.sandbox.profiles import ServiceSpec

    urls = asyncio.run(
        _resolve_service_urls(
            _PortSession(),
            [
                (ServiceSpec(name="code_server", command="code-server", port=8080), 1),
                (ServiceSpec(name="vnc", command="start-vnc", port=6080), 2),
                (ServiceSpec(name="daemon", command="run-daemon", port=None), 3),
            ],
        )
    )
    assert urls == {
        "code_server": "http://127.0.0.1:8080",
        "vnc": "http://127.0.0.1:6080/vnc.html?autoconnect=true&resize=scale",
    }


def test_resolve_service_urls_honors_product_suffix_override():
    from omniagents.core.sandbox.profiles import ServiceSpec

    urls = asyncio.run(
        _resolve_service_urls(
            _PortSession(),
            [(ServiceSpec(name="vnc", command="start-vnc", port=6080), 1)],
            url_suffixes={},
        )
    )
    assert urls == {"vnc": "http://127.0.0.1:6080"}


@pytest.mark.asyncio
async def test_register_execution_environment_routes_to_named_sandbox():
    from omniagents.workspace.sandbox import SandboxWorkspace
    from omniagents.execution import AgentHostResources

    resources = AgentHostResources.create("agent-host-1")
    service = SimpleNamespace(
        workspace_registry=resources.workspaces,
        environment_manager=resources.environments,
    )
    app = SimpleNamespace(state=SimpleNamespace(agent_service=service))
    first_session = SimpleNamespace(name="first")

    descriptor = await _register_execution_environment(
        app,
        environment_id="environment-1",
        workspace_id="workspace-1",
        owner_user_id="user-1",
        session=first_session,
        root="/workspace",
        run_as="1000:1000",
        terminal=SimpleNamespace(
            command="/bin/zsh",
            cwd="/workspace/project",
            user="1000:1000",
        ),
    )

    lease = await resources.environments.acquire("environment-1")
    assert descriptor.workspace_id == "workspace-1"
    assert isinstance(lease.workspace, SandboxWorkspace)
    assert lease.workspace._session is first_session
    assert lease.workspace.root == "/workspace"
    assert lease.workspace._default_user == "1000:1000"
    assert lease.sandbox_runtime.session is first_session
    assert lease.sandbox_runtime.run_as == "1000:1000"
    assert lease.terminal_defaults.shell == "/bin/zsh"
    assert lease.terminal_defaults.cwd == "/workspace/project"
    assert lease.terminal_defaults.user == "1000:1000"
    assert resources.workspaces.get("workspace-1").owner_user_id == "user-1"


# --------------------------------------------------------------------------
# Teardown
# --------------------------------------------------------------------------


class TeardownSession:
    def __init__(self, *, stop_raises=False):
        self.calls = []
        self.stop_raises = stop_raises
        self.state = SimpleNamespace(snapshot=SimpleNamespace(id="snap-1"))

    async def pty_terminate_all(self):
        self.calls.append("pty_terminate_all")

    async def stop_idle_watcher(self):
        self.calls.append("stop_idle_watcher")

    async def unpause(self):
        self.calls.append("unpause")

    async def stop(self):
        self.calls.append("stop")
        if self.stop_raises:
            raise RuntimeError("backend gone")

    async def aclose(self):
        self.calls.append("aclose")


def test_teardown_none_session_is_a_noop():
    asyncio.run(teardown_session(None, [("svc", 1)]))


def test_teardown_full_sequence_and_idempotence():
    session = TeardownSession()
    asyncio.run(teardown_session(session, [("svc", 1)]))
    assert session.calls == [
        "pty_terminate_all",
        "stop_idle_watcher",
        "unpause",
        "stop",
        "aclose",
    ]
    # Second teardown of the same session must not raise (idempotent from the
    # caller's perspective; backend errors are swallowed).
    asyncio.run(teardown_session(session, [("svc", 1)]))
    assert session.calls.count("stop") == 2


def test_teardown_skips_pty_terminate_without_started_services():
    session = TeardownSession()
    asyncio.run(teardown_session(session, []))
    assert session.calls == ["stop_idle_watcher", "unpause", "stop", "aclose"]


def test_teardown_survives_stop_failure():
    session = TeardownSession(stop_raises=True)
    asyncio.run(teardown_session(session, []))
    assert "aclose" in session.calls  # still closed


def test_teardown_discard_snapshot_swaps_in_noop():
    from agents.sandbox.snapshot import NoopSnapshot

    session = TeardownSession()
    asyncio.run(teardown_session(session, [], discard_snapshot=True))
    assert isinstance(session.state.snapshot, NoopSnapshot)
    assert session.state.snapshot.id == "snap-1"
    assert "stop" in session.calls
