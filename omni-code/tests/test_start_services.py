"""Unit tests for idempotent ``serve_cli._start_services`` startup.

Profile init or an image entrypoint may already hold a declared service port,
so relaunching it would bind-clash (e.g. code-server exits 1 with
``EADDRINUSE``). These tests exercise the adopt-if-already-listening path, the
normal launch path, and genuine immediate-exit diagnostics.
"""

import re
from types import SimpleNamespace

import pytest
from omniagents.core.runtime import serve_harness as serve_cli

CODE = serve_cli.ServiceSpec(
    name="code_server",
    command="code-server --bind-addr 0.0.0.0:8080 --auth none /workspace",
    port=8080,
    user="1000:1000",
)
VNC = serve_cli.ServiceSpec(
    name="vnc", command="bash /usr/local/bin/start-vnc.sh", port=6080, user="1000:1000"
)


class FakeServiceSession:
    """Records exec/pty calls; ``listening_ports`` simulates ports already
    bound inside the environment. ``pty_results``
    maps a command substring to a forced ``PtyExecUpdate``-like result."""

    def __init__(self, listening_ports=(), pty_results=None):
        self.listening_ports = set(listening_ports)
        self.exec_calls = []
        self.pty_calls = []
        self._pty_results = pty_results or {}
        self._next_pid = 1000

    async def exec(self, cmd, shell=False, user=None):
        self.exec_calls.append(cmd)
        m = re.search(r"/dev/tcp/127\.0\.0\.1/(\d+)", cmd)
        port = int(m.group(1)) if m else None
        ok = port in self.listening_ports
        return SimpleNamespace(exit_code=0 if ok else 1, stdout=b"", stderr=b"")

    async def pty_exec_start(
        self, cmd, shell=False, tty=False, user=None, yield_time_s=0.0
    ):
        self.pty_calls.append(cmd)
        for key, res in self._pty_results.items():
            if key in cmd:
                return res
        self._next_pid += 1
        return SimpleNamespace(process_id=self._next_pid, output=b"", exit_code=None)


@pytest.mark.asyncio
async def test_cold_container_launches_all_services():
    """Nothing listening → every service is launched with a real pid."""
    s = FakeServiceSession(listening_ports=())
    started = await serve_cli._start_services(s, [CODE, VNC])

    assert len(started) == 2
    assert all(pid != serve_cli._ADOPTED_SERVICE_PID for _, pid in started)
    assert len(s.pty_calls) == 2  # both launched


@pytest.mark.asyncio
async def test_already_listening_service_is_adopted():
    """code-server already bound on 8080 → adopt it (no relaunch), still
    launch the service whose port is free."""
    s = FakeServiceSession(listening_ports={8080})
    started = await serve_cli._start_services(s, [CODE, VNC])

    by_name = {svc.name: pid for svc, pid in started}
    assert by_name["code_server"] == serve_cli._ADOPTED_SERVICE_PID
    assert by_name["vnc"] != serve_cli._ADOPTED_SERVICE_PID
    # Only vnc was (re)launched; code-server was adopted, not started again.
    assert len(s.pty_calls) == 1
    assert all("code-server" not in c for c in s.pty_calls)


@pytest.mark.asyncio
async def test_genuine_failure_surfaces_service_output():
    """An immediate non-zero exit (port free at probe, bind still failed)
    raises with the service's own output — not just a bare exit code."""
    s = FakeServiceSession(
        listening_ports=(),
        pty_results={
            "code-server": SimpleNamespace(
                process_id=None,
                output=b"error listen EADDRINUSE: address already in use 0.0.0.0:8080",
                exit_code=1,
            )
        },
    )
    with pytest.raises(RuntimeError) as ei:
        await serve_cli._start_services(s, [CODE])

    msg = str(ei.value)
    assert "exited immediately with code 1" in msg
    assert "EADDRINUSE" in msg  # the real cause is now visible in the error


@pytest.mark.asyncio
async def test_portless_service_skips_probe_and_launches():
    """A service without a declared port can't be probed, so it's always
    launched (and we don't waste an exec on the probe)."""
    s = FakeServiceSession(listening_ports={9999})
    daemon = serve_cli.ServiceSpec(name="daemon", command="run-daemon", port=None)
    started = await serve_cli._start_services(s, [daemon])

    assert len(s.pty_calls) == 1
    assert started[0][1] != serve_cli._ADOPTED_SERVICE_PID
    assert s.exec_calls == []  # no port probe attempted
