"""Tests for the ``host`` execution target and terminal backend selection.

Covers:
* ``_is_host_profile`` predicate.
* The built-in default profile resolving to the host (no-sandbox) target.
* Terminal backend wiring per target — host PTY for host, session PTY for a
  streaming sandbox, and a loud-error backend (NOT a host shell) for a sandbox
  backend that can't stream a PTY (the ACI regression).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omni_code import serve_cli
from omni_code.sandbox_profile import TerminalSpec, resolve_profile
from omniagents.rpc.terminal_backends import (
    HostPtyBackend,
    PtyOpenError,
    SessionPtyBackend,
)


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class _FakeService:
    terminal_manager = None


class _FakeApp:
    def __init__(self) -> None:
        self.state = type("S", (), {"agent_service": _FakeService()})()


class _StreamingSession:
    """Satisfies PtyStreamingSession (has pty_stream_output + pty_resize)."""

    def pty_stream_output(self, process_id):  # pragma: no cover - shape only
        ...

    async def pty_resize(self, process_id, *, cols, rows):  # pragma: no cover
        ...


class _AciLikeSession:
    """A sandbox session that does NOT support PTY streaming (e.g. ACI)."""


_TERMINAL = TerminalSpec(command="bash -i", user=None, cwd=None)


# --------------------------------------------------------------------------
# Predicate + default profile
# --------------------------------------------------------------------------


def test_is_host_profile():
    assert serve_cli._is_host_profile({"client": {"type": "host"}}) is True
    assert serve_cli._is_host_profile({"client": {"type": "unix_local"}}) is False
    assert serve_cli._is_host_profile({"client": {"type": "docker"}}) is False
    assert serve_cli._is_host_profile({}) is False


def test_default_profile_is_unconfined_host(tmp_path: Path):
    prof = resolve_profile(workspace_dir=tmp_path, project_id=None, explicit_path=None)
    assert (prof.raw.get("client") or {}).get("type") == "host"
    assert prof.raw.get("confine") is False


# --------------------------------------------------------------------------
# Terminal backend selection
# --------------------------------------------------------------------------


def test_host_terminal_manager_uses_host_backend(tmp_path: Path):
    app = _FakeApp()
    serve_cli._install_host_terminal_manager(app, _TERMINAL, default_cwd=str(tmp_path))
    mgr = app.state.agent_service.terminal_manager
    assert mgr is not None
    assert isinstance(mgr.backend, HostPtyBackend)


def test_host_terminal_manager_coerces_path_default_cwd(tmp_path: Path):
    app = _FakeApp()
    serve_cli._install_host_terminal_manager(app, _TERMINAL, default_cwd=tmp_path)
    mgr = app.state.agent_service.terminal_manager
    assert mgr._default_cwd == str(tmp_path)


def test_session_terminal_manager_streaming_uses_session_backend():
    app = _FakeApp()
    serve_cli._install_session_terminal_manager(app, _StreamingSession(), _TERMINAL)
    mgr = app.state.agent_service.terminal_manager
    assert isinstance(mgr.backend, SessionPtyBackend)


@pytest.mark.asyncio
async def test_non_streaming_sandbox_errors_loudly_not_host_shell():
    """ACI regression: a sandbox session without PTY streaming must NOT fall
    back to a host shell. It gets a backend that raises on create."""
    app = _FakeApp()
    serve_cli._install_session_terminal_manager(app, _AciLikeSession(), _TERMINAL)
    mgr = app.state.agent_service.terminal_manager

    assert isinstance(mgr.backend, serve_cli._UnsupportedPtyBackend)
    assert not isinstance(mgr.backend, HostPtyBackend)

    with pytest.raises(PtyOpenError):
        await mgr.backend.start(shell="bash -i", cwd=None, cols=80, rows=24)


# --------------------------------------------------------------------------
# Instruction workspace path reflects the authoritative workspace_root
# --------------------------------------------------------------------------
#
# The workspace_root that reaches build_coding_context is guaranteed correct
# at the source: omni serve forces it to the sandbox manifest root via
# AgentService(workspace_root_override=…) (see omniagents
# tests/unit/core/agents/test_workspace_root_override.py). build_coding_context
# therefore just reflects whatever it is given — no manifest lookup, no
# fallback.


def test_instruction_workspace_reflects_given_workspace_root():
    """The [Workspace] line shows the workspace_root it is handed — which, for
    a sandbox run, is the container manifest root forced by the server."""
    from omni_code.agents.omni.context import format_agents_md_block

    block = format_agents_md_block({"workspace_root": "/workspace/myproject"})
    assert "[Workspace]\n/workspace/myproject" in block

    block = format_agents_md_block({"workspace_root": "/home/user/proj"})
    assert "[Workspace]\n/home/user/proj" in block


# --------------------------------------------------------------------------
# Picker dir listing browses the container when a sandbox session is live
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fs_list_dir_browses_container_when_sandboxed():
    """With a live sandbox session, fs_list_dir lists the CONTAINER (via the
    session), defaulting to the manifest root and returning absolute container
    paths — so the picker navigates the filesystem the agent's tools see."""
    # fs_list_dir is the framework builtin; importing the omni context
    # installs the providers that surface omni serve's published state
    # (agent_workspace_state) to it.
    import omni_code.agents.omni.context  # noqa: F401
    import omniagents.builtin.server_functions.workspace_info as fsmod
    from omni_code import agent_workspace_state as aws

    class _SdkEntry:
        def __init__(self, name, is_dir):
            self.name = name
            self.path = name
            self.is_dir = is_dir
            self.size = None
            self.mtime = None

    class _Session:
        _inner = None

        async def ls(self, path, user=None):
            return [_SdkEntry("src", True), _SdkEntry("README.md", False)]

    aws.set_manifest_root("/workspace/proj")
    aws.set_sandbox_session(_Session())
    try:
        res = await fsmod.fs_list_dir(None)  # default base = manifest root
        assert res["path"] == "/workspace/proj"
        names = [e["name"] for e in res["entries"]]
        assert "src" in names  # directory included
        assert "README.md" not in names  # files excluded by default
        src = next(e for e in res["entries"] if e["name"] == "src")
        assert src["path"] == "/workspace/proj/src"  # absolute container path
        assert src["is_dir"] is True

        # A host path that leaked through (e.g. the launcher's local project
        # dir) is clamped to the manifest root rather than erroring.
        res2 = await fsmod.fs_list_dir("/home/emm/Omni/Workspace")
        assert res2["path"] == "/workspace/proj"
        # At the manifest root, "up" is suppressed (can't escape the boundary).
        assert res2["parent"] is None
    finally:
        aws.set_sandbox_session(None)
        aws.set_manifest_root(None)
