"""Tests for the ``process`` sandbox backend (omni-sandbox wrapped execs).

All construction/argv tests run WITHOUT the real binary — a fake
executable file stands in — so they exercise:

* factory wiring (``client.type: process``),
* binary discovery order (explicit key → ``OMNI_SANDBOX_BIN`` → PATH)
  and the fail-closed missing-binary construction error,
* fail-closed rejection of ``network_allow`` host allowlists,
* the exact ``omni-sandbox exec`` argv built from a profile's mounts,
  network mode, and protected subpaths,
* lifecycle probing: pause/unpause report unsupported like unix_local.

One end-to-end test proves ``echo`` runs wrapped; it is skipped unless
bwrap and a real omni-sandbox binary are both available locally.
"""

from __future__ import annotations

import sys

import pytest

if sys.platform == "win32":
    pytest.skip(
        "the process sandbox backend (omni-sandbox/bwrap) is POSIX-only",
        allow_module_level=True,
    )

import os
import shutil
import uuid
from pathlib import Path

import pytest

from agents.sandbox.manifest import Manifest
from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxSessionState
from agents.sandbox.snapshot import resolve_snapshot

from omniagents.core.sandbox.factory import build_sandbox_client
from omniagents.core.sandbox.lifecycle_session import (
    LifecycleSandboxSession,
    SandboxLifecycleUnsupported,
)
from omniagents.core.sandbox.process_extended import (
    ProcessSandboxClient,
    ProcessSandboxSession,
    resolve_omni_sandbox_binary,
)


@pytest.fixture()
def fake_binary(tmp_path: Path) -> str:
    """An executable file that stands in for omni-sandbox."""
    path = tmp_path / "omni-sandbox"
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return str(path)


@pytest.fixture(autouse=True)
def _no_ambient_binary(monkeypatch):
    """Isolate discovery from the developer's real environment."""
    monkeypatch.delenv("OMNI_SANDBOX_BIN", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: None)


def _state(workspace: Path) -> UnixLocalSandboxSessionState:
    session_id = uuid.uuid4()
    return UnixLocalSandboxSessionState(
        session_id=session_id,
        manifest=Manifest(root=str(workspace)),
        snapshot=resolve_snapshot(None, str(session_id)),
        workspace_root_owned=False,
        exposed_ports=(),
    )


# ------------------------------ discovery ------------------------------ #


def test_explicit_binary_key_wins(fake_binary, monkeypatch, tmp_path):
    other = tmp_path / "other-omni-sandbox"
    other.write_text("#!/bin/sh\n")
    other.chmod(0o755)
    monkeypatch.setenv("OMNI_SANDBOX_BIN", str(other))
    assert resolve_omni_sandbox_binary(fake_binary) == fake_binary


def test_env_var_discovery(fake_binary, monkeypatch):
    monkeypatch.setenv("OMNI_SANDBOX_BIN", fake_binary)
    assert resolve_omni_sandbox_binary() == fake_binary


def test_path_discovery(fake_binary, monkeypatch):
    monkeypatch.setattr(
        shutil, "which", lambda name: fake_binary if name == "omni-sandbox" else None
    )
    assert resolve_omni_sandbox_binary() == fake_binary


def test_missing_binary_is_construction_time_error():
    with pytest.raises(FileNotFoundError) as excinfo:
        ProcessSandboxClient()
    message = str(excinfo.value)
    # The error must name every install option.
    assert "sandbox.client.binary" in message
    assert "OMNI_SANDBOX_BIN" in message
    assert "PATH" in message
    assert "omni-sandbox" in message


def test_explicit_binary_must_be_executable(tmp_path):
    not_exec = tmp_path / "omni-sandbox"
    not_exec.write_text("not a binary")
    not_exec.chmod(0o644)
    with pytest.raises(FileNotFoundError, match="not an executable"):
        ProcessSandboxClient(binary=str(not_exec))


def test_factory_builds_process_client(fake_binary):
    client, options = build_sandbox_client(
        {"client": {"type": "process", "binary": fake_binary}}
    )
    assert isinstance(client, ProcessSandboxClient)
    assert options is None


# --------------------------- fail-closed net --------------------------- #


def test_network_allowlist_fails_closed(fake_binary):
    with pytest.raises(ValueError) as excinfo:
        ProcessSandboxClient(
            binary=fake_binary,
            network_allow=["api.example.com", "pypi.org"],
        )
    message = str(excinfo.value)
    assert "api.example.com, pypi.org" in message
    assert "cannot enforce" in message
    assert "host-level network filtering" in message
    # Must point at the escape hatches, mirroring governance.py's shape.
    assert "container backend" in message


def test_network_allowlist_rejected_even_with_unrestricted(fake_binary):
    with pytest.raises(ValueError):
        ProcessSandboxClient(
            binary=fake_binary,
            network="unrestricted",
            network_allow=["example.com"],
        )


def test_invalid_network_value_rejected(fake_binary):
    with pytest.raises(ValueError, match="none.*unrestricted"):
        ProcessSandboxClient(binary=fake_binary, network="allowlist")


# ---------------------------- argv mapping ----------------------------- #


def test_argv_wraps_command_with_defaults(fake_binary, tmp_path):
    client = ProcessSandboxClient(binary=fake_binary)
    inner = client._make_inner_session(_state(tmp_path))
    assert isinstance(inner, ProcessSandboxSession)

    argv = inner._confined_exec_command(
        command_parts=["sh", "-c", "echo hi"],
        workspace_root=tmp_path,
        env={},
    )
    # Network blocked by default (no --net); no mounts; protected
    # subpaths omitted so the binary's own defaults apply.
    assert argv == [
        fake_binary,
        "exec",
        "--workspace",
        str(tmp_path),
        "--",
        "sh",
        "-c",
        "echo hi",
    ]


def test_argv_maps_mounts_net_and_protect(fake_binary, tmp_path):
    client = ProcessSandboxClient(
        binary=fake_binary,
        ro_binds=["/opt/tools", "/opt/venv"],
        rw_binds=["/home/user/.cache/thing"],
        network="unrestricted",
        protected_subpaths=[".git", ".env"],
    )
    inner = client._make_inner_session(_state(tmp_path))

    argv = inner._confined_exec_command(
        command_parts=["/bin/true"],
        workspace_root=tmp_path,
        env={},
    )
    # Canonical order (shared with the governance relaunch builder):
    # workspace, net, protect, then binds in caller order.
    assert argv == [
        fake_binary,
        "exec",
        "--workspace",
        str(tmp_path),
        "--net",
        "--protect",
        ".git",
        "--protect",
        ".env",
        "--ro-bind",
        "/opt/tools",
        "--ro-bind",
        "/opt/venv",
        "--rw-bind",
        "/home/user/.cache/thing",
        "--",
        "/bin/true",
    ]


def test_protect_values_never_comma_joined(fake_binary, tmp_path):
    client = ProcessSandboxClient(
        binary=fake_binary, protected_subpaths=[".git", ".env", ".codex"]
    )
    inner = client._make_inner_session(_state(tmp_path))
    argv = inner._confined_exec_command(
        command_parts=["/bin/true"], workspace_root=tmp_path, env={}
    )
    for value in argv:
        assert "," not in value, f"comma-joined argv value: {value!r}"
    assert argv.count("--protect") == 3


def test_empty_protected_subpaths_still_omit_flag(fake_binary, tmp_path):
    # Explicit empty list: caller wants no --protect flags of its own;
    # the binary's defaults then apply (same wire shape as None today).
    client = ProcessSandboxClient(binary=fake_binary, protected_subpaths=[])
    inner = client._make_inner_session(_state(tmp_path))
    argv = inner._confined_exec_command(
        command_parts=["/bin/true"], workspace_root=tmp_path, env={}
    )
    assert "--protect" not in argv


# ------------------------------ lifecycle ------------------------------ #


@pytest.mark.asyncio
async def test_pause_unpause_report_unsupported(fake_binary, tmp_path):
    client = ProcessSandboxClient(binary=fake_binary)
    session = await client.create(manifest=Manifest(root=str(tmp_path)))
    assert isinstance(session, LifecycleSandboxSession)
    assert not session.supports_pause()
    assert not session.supports_unpause()
    with pytest.raises(SandboxLifecycleUnsupported):
        await session.pause()
    assert await session.paused() is False


# ------------------------------ end-to-end ----------------------------- #


def _real_binary() -> str | None:
    env_bin = os.environ.get("OMNI_SANDBOX_BIN")
    if env_bin and Path(env_bin).is_file() and os.access(env_bin, os.X_OK):
        return env_bin
    return shutil.which("omni-sandbox")


# Captured at import time, before the autouse fixture isolates discovery.
_REAL_BINARY = _real_binary()
_HAVE_BWRAP = shutil.which("bwrap") is not None


@pytest.mark.asyncio
@pytest.mark.skipif(
    not (_REAL_BINARY and _HAVE_BWRAP),
    reason="requires bwrap and a built omni-sandbox binary (set OMNI_SANDBOX_BIN)",
)
async def test_echo_runs_wrapped_end_to_end(tmp_path):
    # Explicit binary path sidesteps the autouse discovery isolation.
    client = ProcessSandboxClient(binary=_REAL_BINARY)
    session = await client.create(manifest=Manifest(root=str(tmp_path)))
    await session.start()
    try:
        result = await session.exec("echo hello-from-omni-sandbox")
        assert result.exit_code == 0
        assert b"hello-from-omni-sandbox" in result.stdout
        # And the workspace really is the sandbox cwd view.
        marker = await session.exec("pwd")
        assert str(tmp_path).encode() in marker.stdout
    finally:
        await session.stop()
