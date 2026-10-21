"""Smoke test: long-running PTY processes survive intervening session work.

Architectural rationale: the omni-code launcher is evaluating replacing its
bespoke Docker-management code with ``SandboxAgent`` + ``SandboxSession`` from
the agents SDK. That migration only pays off if a session can host long-lived
background daemons (code-server, VNC, terminal tabs) that stay alive while
the agent does unrelated work via ``session.exec`` / ``session.read`` /
``session.write``.

This test proves the claim using the ``unix_local`` backend so it runs without
Docker. The session contract is the same for the docker / e2b / modal / etc.
backends — all routed through ``BaseSandboxSession``.
"""

from __future__ import annotations

import sys

import pytest

if sys.platform == "win32":
    pytest.skip(
        "UnixLocalSandbox is POSIX-only; Windows hosts use docker/wasm sandboxes or host mode",
        allow_module_level=True,
    )

import asyncio
import io
from pathlib import PurePosixPath

import pytest

from agents.sandbox.sandboxes import (
    UnixLocalSandboxClient,
    UnixLocalSandboxClientOptions,
)

# Daemon writes a tick every TICK_INTERVAL seconds. Short so the test
# finishes fast; bump it (and the sleeps below) if CI is flaky.
TICK_INTERVAL = 0.25


def _b(s: str) -> io.BytesIO:
    return io.BytesIO(s.encode())


async def _read_heartbeat_count(session, heartbeat_path: str) -> int:
    result = await session.exec(
        "sh", "-c", f"wc -l < {heartbeat_path} 2>/dev/null || echo 0", shell=False
    )
    if result.exit_code != 0:
        return 0
    return int(result.stdout.decode().strip() or "0")


@pytest.mark.asyncio
async def test_pty_daemon_survives_intervening_session_work():
    # unix_local replaces the default manifest root (/workspace) with a host
    # tmpdir, so read the real root from the session after create() and
    # parameterize all sandbox paths off of it.
    client = UnixLocalSandboxClient()
    options = UnixLocalSandboxClientOptions()
    session = await client.create(options=options)
    await session.start()
    root = session.state.manifest.root
    heartbeat_path = f"{root}/heartbeat"
    scratch_root = f"{root}/scratch"
    try:
        await session.exec("touch", heartbeat_path, shell=False)

        # Phase A — launch the daemon. tty=False because we don't need a
        # terminal; we want a tracked background process the session can
        # report on later via pty_write_stdin polling.
        daemon_cmd = (
            f"i=0; while true; do echo $i >> {heartbeat_path}; "
            f"i=$((i+1)); sleep {TICK_INTERVAL}; done"
        )
        update = await session.pty_exec_start(
            daemon_cmd, shell=True, tty=False, yield_time_s=0.0
        )
        assert (
            update.exit_code is None
        ), f"daemon exited immediately with code {update.exit_code}"
        daemon_pid = update.process_id
        assert daemon_pid is not None

        # Wait for a few ticks so a baseline exists
        await asyncio.sleep(TICK_INTERVAL * 4)
        baseline = await _read_heartbeat_count(session, heartbeat_path)
        assert baseline >= 1, f"daemon never wrote a heartbeat (count={baseline})"

        # Phase B — simulate Runner.run doing tool work between turns:
        # bursts of unrelated session.exec / read / write / mkdir calls,
        # the same shape as a Filesystem or Shell capability tool body.
        for i in range(5):
            await session.exec("mkdir", "-p", f"{scratch_root}/{i}", shell=False)
            await session.write(
                PurePosixPath(f"{scratch_root}/{i}/note.txt"), _b(f"turn {i}")
            )
            stream = await session.read(PurePosixPath(f"{scratch_root}/{i}/note.txt"))
            try:
                assert stream.read() == f"turn {i}".encode()
            finally:
                close = getattr(stream, "close", None)
                if callable(close):
                    close()
            await session.exec("ls", scratch_root, shell=False)

        # Phase C — heartbeat must have advanced; the daemon kept ticking
        # alongside the foreground session work.
        after = await _read_heartbeat_count(session, heartbeat_path)
        assert after > baseline, (
            f"daemon stopped during intervening work "
            f"(baseline={baseline}, after={after})"
        )

        # Phase D — explicit liveness check via the SDK: poll the PTY with
        # an empty stdin write. exit_code must still be None.
        poll = await session.pty_write_stdin(
            session_id=daemon_pid, chars="", yield_time_s=0.0
        )
        assert (
            poll.exit_code is None
        ), f"PTY reports exited (code={poll.exit_code}) after session work"

    finally:
        try:
            await session.pty_terminate_all()
        except Exception:
            pass
        await session.stop()
        await session.aclose()
