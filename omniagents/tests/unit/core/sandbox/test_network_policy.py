"""Tests for the in-container egress policy (baseline allowlist + offline toggle)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from omniagents.core.sandbox import network_policy
from omniagents.core.sandbox.network_policy import (
    BASELINE_CHAIN,
    BLOCK_CHAIN,
    BLOCK_MARKER_PATH,
    apply_baseline_egress,
    build_baseline_script,
    build_block_script,
    build_unblock_script,
    network_blocked,
    set_network_blocked,
)


@dataclass
class _ExecResult:
    exit_code: int = 0
    stdout: bytes = b""
    stderr: bytes = b""


@dataclass
class _Session:
    results: list = field(default_factory=list)
    calls: list = field(default_factory=list)

    async def exec(self, cmd, *, shell=False, user=None):
        self.calls.append((cmd, shell, user))
        if self.results:
            return self.results.pop(0)
        return _ExecResult()


# ------------------------------ script shape ---------------------------- #


def test_baseline_script_covers_allowlist_entries():
    script = build_baseline_script("api.openai.com, 10.0.0.0/8,, ")
    # Chain is rebuilt idempotently and terminates in a REJECT.
    assert f"iptables -N {BASELINE_CHAIN}" in script
    assert f"iptables -F {BASELINE_CHAIN}" in script
    assert f"iptables -A {BASELINE_CHAIN} -j REJECT" in script
    # Hostname entries resolve at apply time; CIDRs pass straight through.
    assert "getent ahosts api.openai.com" in script
    assert f"iptables -A {BASELINE_CHAIN} -d 10.0.0.0/8 -j ACCEPT" in script
    # Loopback, established, DNS, and the docker host gateway stay open.
    assert f"iptables -A {BASELINE_CHAIN} -o lo -j ACCEPT" in script
    assert "--dport 53" in script
    assert "host.docker.internal" in script
    # The jump is appended, not inserted — the offline block chain must
    # keep precedence when both are installed.
    assert f"iptables -A OUTPUT -j {BASELINE_CHAIN}" in script
    assert f"iptables -I OUTPUT 1 -j {BASELINE_CHAIN}" not in script


def test_block_script_keeps_inbound_services_and_writes_marker():
    script = build_block_script()
    # Inserted at the top of OUTPUT so it precedes any baseline allowlist.
    assert f"iptables -I OUTPUT 1 -j {BLOCK_CHAIN}" in script
    # Reply-direction conntrack keeps code-server/VNC answering, with a
    # plain-established fallback for images without the conntrack match.
    assert "--ctdir REPLY" in script
    assert "-m state --state ESTABLISHED,RELATED" in script
    # No DNS in offline mode.
    assert "--dport 53" not in script
    # Durable marker in the writable layer (NOT tmpfs /run).
    assert BLOCK_MARKER_PATH in script
    assert BLOCK_MARKER_PATH.startswith("/var/lib/")
    assert f"touch {BLOCK_MARKER_PATH}" in script


def test_unblock_script_removes_jump_and_marker():
    script = build_unblock_script()
    assert f"iptables -D OUTPUT -j {BLOCK_CHAIN}" in script
    assert f"rm -f {BLOCK_MARKER_PATH}" in script


def test_scripts_quote_hostile_allowlist_entries():
    script = build_baseline_script("evil; rm -rf /,$(reboot)")
    assert "'evil; rm -rf /'" in script
    assert "'$(reboot)'" in script


# ------------------------------ exec helpers ---------------------------- #


def test_apply_baseline_noop_without_allowlist():
    session = _Session()
    asyncio.run(apply_baseline_egress(session, None))
    asyncio.run(apply_baseline_egress(session, "  "))
    assert session.calls == []


def test_apply_baseline_execs_as_root():
    session = _Session()
    asyncio.run(apply_baseline_egress(session, "api.openai.com"))
    cmd, shell, user = session.calls[0]
    assert shell is True
    assert user == "root"
    assert BASELINE_CHAIN in cmd


def test_apply_baseline_fails_closed_on_error():
    session = _Session(results=[_ExecResult(exit_code=2, stderr=b"iptables: boom")])
    with pytest.raises(RuntimeError, match="network allowlist.*boom"):
        asyncio.run(apply_baseline_egress(session, "api.openai.com"))


def test_set_network_blocked_runs_matching_script():
    session = _Session()
    asyncio.run(set_network_blocked(session, True))
    asyncio.run(set_network_blocked(session, False))
    assert f"touch {BLOCK_MARKER_PATH}" in session.calls[0][0]
    assert f"rm -f {BLOCK_MARKER_PATH}" in session.calls[1][0]
    assert all(user == "root" for (_, _, user) in session.calls)


def test_network_blocked_reads_marker_exit_code():
    assert asyncio.run(network_blocked(_Session(results=[_ExecResult(0)]))) is True
    assert asyncio.run(network_blocked(_Session(results=[_ExecResult(1)]))) is False


def test_network_blocked_errors_read_as_unblocked():
    class _Broken:
        async def exec(self, *a, **k):
            raise RuntimeError("gone")

    assert asyncio.run(network_policy.network_blocked(_Broken())) is False
