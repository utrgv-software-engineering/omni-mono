"""The wasm sandbox: bashkit shell + pyodide Python over one workspace.

The live tests boot the real pyodide Node worker (seconds, not
milliseconds), so they share one module-scoped session and are skipped
when bashkit, Node, or the worker's node_modules are unavailable —
mirroring the bashkit suite's opt-out posture.
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
from pathlib import Path

import pytest
import pytest_asyncio

from agents.sandbox.manifest import Manifest

_HAS_BASHKIT = importlib.util.find_spec("bashkit") is not None
_HAS_NODE = shutil.which("node") is not None
_WORKER_DEPS = (
    Path(__file__).parents[4]
    / "omniagents"
    / "workspace"
    / "pyodide_worker"
    / "node_modules"
    / "pyodide"
).is_dir()

pytestmark = pytest.mark.skipif(
    not (_HAS_BASHKIT and _HAS_NODE and _WORKER_DEPS),
    reason="wasm sandbox needs bashkit, node, and pyodide_worker node_modules",
)


@pytest_asyncio.fixture(scope="module")
async def session(tmp_path_factory):
    from omniagents.core.sandbox.wasm import WasmSandboxClient

    host_dir = tmp_path_factory.mktemp("wasmbox")
    client = WasmSandboxClient(workspace_dir=str(host_dir), git=True)
    sess = await client.create(manifest=Manifest(root="/workspace"))
    await sess.start()
    # Host backing dir for tests that must verify durability on disk.
    sess._test_host_dir = str(host_dir)
    try:
        yield sess
    finally:
        await sess.aclose()


@pytest.mark.asyncio(loop_scope="module")
async def test_shell_and_python_share_one_workspace(session):
    r = await session.exec("echo hello > note.txt && cat note.txt", shell=True)
    assert (r.exit_code, r.stdout) == (0, b"hello\n")

    # Python (pyodide, separate process) reads what the shell (bashkit,
    # in-process) wrote — one host directory underneath both.
    r = await session.exec(
        "python -c \"print(open('note.txt').read().strip() + ' via python')\"",
        shell=True,
    )
    assert (r.exit_code, r.stdout) == (0, b"hello via python")

    r = await session.exec(
        "python -c \"open('back.txt','w').write('42')\" && cat back.txt",
        shell=True,
    )
    assert (r.exit_code, r.stdout) == (0, b"42")


@pytest.mark.asyncio(loop_scope="module")
async def test_shell_and_python_share_the_workspace_namespace(session):
    # Docker's property: one uniform /workspace namespace in every
    # runtime, and the host path never visible inside the sandbox.
    r = await session.exec(
        "cd /workspace && pwd && python -c 'import os; print(os.getcwd())'",
        shell=True,
    )
    assert r.exit_code == 0, r.stderr
    assert r.stdout.decode().strip().splitlines() == ["/workspace", "/workspace"]
    # The host backing dir must never leak into sandbox output — not from
    # the shell, not from python, and not from host-side builtin messages
    # (git's "Initialized empty Git repository in …" was a real leak).
    host_dir = session._test_host_dir
    probes = (
        "pwd",
        "python -c 'import os; print(os.getcwd())'",
        "cd /workspace && git init",
    )
    for probe in probes:
        r = await session.exec(probe, shell=True)
        combined = r.stdout.decode() + r.stderr.decode()
        assert host_dir not in combined, probe
    r = await session.exec("cd /workspace && git init 2>&1 | head -1", shell=True)
    assert b"/workspace" in r.stdout


@pytest.mark.asyncio(loop_scope="module")
async def test_python_scripts_argv_and_subdir_cwd(session):
    r = await session.exec(
        "mkdir -p sub && echo 'import sys; print(sys.argv[1:])' > sub/s.py "
        "&& cd sub && python s.py a b",
        shell=True,
    )
    assert r.exit_code == 0, r.stderr
    assert r.stdout == b"['a', 'b']"


@pytest.mark.asyncio(loop_scope="module")
async def test_exit_codes_do_not_poison_the_interpreter(session):
    # Nonzero SystemExit surfaces as failure...
    r = await session.exec("python -c 'import sys; sys.exit(3)'", shell=True)
    assert r.exit_code == 1
    assert b"SystemExit: 3" in r.stderr
    # ...without breaking later invocations (a re-raised SystemExit would
    # poison pyodide's runtime), and sys.exit(0) is success.
    r = await session.exec("python -c 'import sys; sys.exit(0)'", shell=True)
    assert r.exit_code == 0
    r = await session.exec("python -c 'print(21*2)'", shell=True)
    assert (r.exit_code, r.stdout) == (0, b"42")


@pytest.mark.asyncio(loop_scope="module")
async def test_missing_script_and_no_args_fail_like_python(session):
    r = await session.exec("python nope.py", shell=True)
    assert r.exit_code == 2
    assert b"can't open file" in r.stderr
    r = await session.exec("python", shell=True)
    assert r.exit_code == 2
    assert b"interactive mode is unavailable" in r.stderr


@pytest.mark.asyncio(loop_scope="module")
async def test_exec_with_user_identity_is_a_noop(session):
    # Serve's init commands and credential setup pass ``user="root"``. The
    # SDK base would wrap that in ``sudo -u root --``, which exits 127 in
    # bashkit (no sudo, no users) — the engine drops the identity instead.
    r = await session.exec("echo ok", shell=True, user="root")
    assert (r.exit_code, r.stdout) == (0, b"ok\n")


@pytest.mark.asyncio(loop_scope="module")
async def test_seed_tar_extract_is_host_side(session, tmp_path):
    # Workspace seeding (PermissiveLocalDir / LocalGitArchive) writes one
    # staging tar and execs ``tar xf <staging> -C <dest>`` — intercepted
    # and extracted host-side, since bashkit has no real tar.
    import io
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        payload = io.BytesIO(b"seeded")
        info = tarfile.TarInfo("seeded.txt")
        info.size = 6
        archive.addfile(info, payload)
    staging = "/workspace/.seed.tar"
    await session.write(staging, io.BytesIO(buffer.getvalue()))
    r = await session.exec("tar", "xf", staging, "-C", "/workspace", shell=False)
    assert r.exit_code == 0, r.stderr
    # Absolute path: the module-scoped session's shell cwd persists across
    # tests (by design), so don't assume we are still at the root.
    r = await session.exec("cat /workspace/seeded.txt", shell=True)
    assert (r.exit_code, r.stdout) == (0, b"seeded")
    # Outside the workspace: refused, not extracted.
    r = await session.exec("tar", "xf", staging, "-C", "/etc", shell=False)
    assert r.exit_code == 1
    assert b"outside workspace" in r.stderr


@pytest.mark.asyncio(loop_scope="module")
async def test_each_invocation_gets_a_fresh_namespace(session):
    await session.exec("python -c 'X = 5'", shell=True)
    r = await session.exec("python -c 'print(X)'", shell=True)
    assert r.exit_code == 1
    assert b"NameError" in r.stderr


@pytest.mark.asyncio(loop_scope="module")
async def test_default_session_boots_offline(session):
    # Network is OFF by default: the sandbox boots contained-and-offline
    # (the auto-approve basis) but stays toggleable. The gate is a wrapper,
    # so js.fetch still imports — calling it is what's refused.
    assert session.network_toggleable
    assert session.network_enabled is False
    r = await session.exec("curl https://example.com", shell=True)
    assert r.exit_code == 1
    assert b"network is disabled" in r.stderr
    r = await session.exec(
        "python -c 'from js import fetch; fetch(\"https://example.com\")'",
        shell=True,
    )
    assert r.exit_code == 1
    assert b"sandbox is offline" in r.stderr


@pytest.mark.asyncio
async def test_explicit_offline_gates_python_egress(tmp_path):
    # `network: none` is the opt-out that makes the sandbox offline. The
    # js.fetch egress must then be gone, or the dropped-NETWORK capability
    # (and the auto-approval that rides on it) would be a lie.
    from omniagents.core.sandbox.wasm import WasmSandboxClient

    client = WasmSandboxClient(workspace_dir=str(tmp_path), network="none")
    sess = await client.create(manifest=Manifest(root="/workspace"))
    await sess.start()
    try:
        r = await sess.exec(
            "python -c 'from js import fetch; print(fetch)'", shell=True
        )
        assert r.exit_code == 1
        assert b"cannot import name 'fetch'" in r.stderr
    finally:
        await sess.aclose()


@pytest.mark.asyncio(loop_scope="module")
async def test_live_network_toggle_gates_both_runtimes(session):
    # sandbox.set_network flips the shell's curl/git builtins and the
    # pyodide worker's egress globals in one move — no iptables involved.
    # Default boots blocked; enabling restores the configured policy.
    assert session.network_toggleable
    assert session.network_enabled is False
    await session.set_network_enabled(True)
    try:
        assert session.network_enabled
        # The gate no longer short-circuits — curl proceeds to its own
        # argv handling (no URL → usage error, not the offline message).
        r = await session.exec("curl", shell=True)
        assert b"network is disabled" not in r.stderr
        # Python: the wrapped fetch passes through (truthy, callable).
        r = await session.exec(
            "python -c 'from js import fetch; print(bool(fetch))'", shell=True
        )
        assert r.exit_code == 0, r.stderr
        assert r.stdout.strip() == b"True"
    finally:
        await session.set_network_enabled(False)
    assert session.network_enabled is False
    # Blocked again: git remote subcommands refused, local git still works.
    r = await session.exec("git clone https://example.com/x.git", shell=True)
    assert r.exit_code == 1
    assert b"network is disabled" in r.stderr
    r = await session.exec("git init toggle-repo", shell=True)
    assert r.exit_code == 0, r.stderr


@pytest.mark.asyncio
async def test_explicit_offline_session_refuses_toggle(tmp_path):
    # `network: none` registered the sandbox as contained-and-offline —
    # the auto-approval basis — so there is deliberately no way to grant
    # egress mid-session.
    from omniagents.core.sandbox.wasm import WasmSandboxClient

    client = WasmSandboxClient(workspace_dir=str(tmp_path), network="none")
    sess = await client.create(manifest=Manifest(root="/workspace"))
    await sess.start()
    try:
        assert sess.network_toggleable is False
        with pytest.raises(RuntimeError, match="explicitly offline"):
            await sess.set_network_enabled(True)
    finally:
        await sess.aclose()


def test_pyodide_network_policy_mapping():
    from omniagents.core.sandbox.wasm import (
        _pyodide_network_policy,
        _wasm_network_boots_blocked,
    )

    # Absent: the CONFIGURED (toggle-restorable) policy is open...
    assert _pyodide_network_policy(None) == ("open", ())
    assert _pyodide_network_policy(True) == ("open", ())
    assert _pyodide_network_policy({"allow_all": True}) == ("open", ())
    # Explicit opt-out.
    assert _pyodide_network_policy(False) == ("none", ())
    assert _pyodide_network_policy("none") == ("none", ())
    # Allowlist.
    assert _pyodide_network_policy({"allow": ["a.com", "b.com"]}) == (
        "allow",
        ("a.com", "b.com"),
    )
    # A restriction naming nothing concrete reads as opt-out, not silent open.
    assert _pyodide_network_policy({"allow": []}) == ("none", ())
    assert _pyodide_network_policy({}) == ("none", ())

    # ...but the session BOOTS offline unless the profile grants network
    # explicitly — off by default.
    assert _wasm_network_boots_blocked(None) is True
    assert _wasm_network_boots_blocked(True) is False
    assert _wasm_network_boots_blocked({"allow_all": True}) is False
    assert _wasm_network_boots_blocked({"allow": ["a.com"]}) is False
    assert _wasm_network_boots_blocked("none") is True
    assert _wasm_network_boots_blocked(False) is True


def test_factory_builds_wasm_client():
    from omniagents.core.sandbox.factory import build_sandbox_client
    from omniagents.core.sandbox.wasm import WasmSandboxClient

    client, options = build_sandbox_client(
        {"client": {"type": "wasm", "git": True, "network": {"allow": ["example.com"]}}}
    )
    assert isinstance(client, WasmSandboxClient)
    assert options is None


def test_python_dispatch_paths_confine_to_workspace(tmp_path):
    from omniagents.core.sandbox.wasm import WasmSandboxSession

    session = WasmSandboxSession(
        root="/workspace",
        host_dir=str(tmp_path),
        manifest=Manifest(root="/workspace"),
    )
    # VFS paths under /workspace resolve to the host backing dir...
    assert session._confined_mounted_root("/workspace") == str(tmp_path)
    assert session._confined_mounted_root("/workspace/a/b.py") == str(
        tmp_path / "a" / "b.py"
    )
    # ...and back again for anything the sandbox user sees.
    assert session._virtual_path(str(tmp_path / "a" / "b.py")) == "/workspace/a/b.py"
    # Outside the workspace: refused.
    assert session._confined_mounted_root("/etc/passwd") is None
    assert session._confined_mounted_root("/workspace/../x") is None


def test_client_validates_root_and_workspace_dir(tmp_path):
    from omniagents.core.sandbox.wasm import WasmSandboxClient

    with pytest.raises(ValueError, match="absolute VFS"):
        asyncio.run(
            WasmSandboxClient(workspace_dir=str(tmp_path)).create(
                manifest=Manifest(root="workspace")
            )
        )
    with pytest.raises(ValueError, match="workspace_dir"):
        asyncio.run(WasmSandboxClient().create(manifest=Manifest(root="/workspace")))
