"""Unit tests for the ACI exec-channel sentinel protocol (no Azure needed).

A fake WebSocket simulates the container PTY: it echoes the command line back
(as a real terminal does) and emits the marker/base64 frames the channel
expects. This pins the parsing — exit-code extraction, stdout/stderr
separation, binary-safety, and echo-robustness — so it can't regress without
hitting a real subscription.
"""

from __future__ import annotations

import asyncio
import base64
import re

from omniagents.core.sandbox.aci_engine import AciExecChannel


class FakeExecWs:
    """Scripts PTY responses by inspecting what the channel sends.

    Configured with the bytes each redirect file should contain and the exit
    code. Every response includes the echoed input line first (literal ``%d`` /
    marker-in-echo) so the channel's regexes prove they ignore echo.
    """

    def __init__(self, *, exit_code: int, stdout: bytes, stderr: bytes) -> None:
        self._exit = exit_code
        self._files = {"o": stdout, "e": stderr}
        self._frames: list[str] = []

    async def send(self, data: str) -> None:
        echo = data.rstrip("\n")  # the terminal echoes the typed line
        mx = re.search(r"__OMNIX_([0-9a-f]+)__%d", data)
        if mx:
            tok = mx.group(1)
            self._frames.append(echo + "\n")
            self._frames.append(f"__OMNIX_{tok}__{self._exit}\n")
            return
        mb = re.search(r"__OMNS_([0-9a-f]+)__; base64 (\S+)", data)
        if mb:
            tok, path = mb.group(1), mb.group(2)
            which = "o" if path.endswith(f".omni_o_{tok}") else "e"
            payload = base64.b64encode(self._files[which]).decode()
            self._frames.append(echo + "\n")
            # real output: START\n <b64?> \n (extra echo) \n END\n
            body = (payload + "\n") if payload else ""
            self._frames.append(f"__OMNS_{tok}__\n{body}\n__OMNE_{tok}__\n")
            return
        # rm cleanup or anything else: no output
        return

    async def recv(self) -> str:
        if not self._frames:
            raise AssertionError("channel read more frames than the fake produced")
        return self._frames.pop(0)


def _run(exit_code: int, stdout: bytes, stderr: bytes):
    chan = AciExecChannel(FakeExecWs(exit_code=exit_code, stdout=stdout, stderr=stderr))
    return asyncio.run(chan.run("any-cmd"))


def test_success_with_stdout_and_empty_stderr():
    r = _run(0, b"hello\nLinux\n", b"")
    assert r.exit_code == 0
    assert r.stdout == b"hello\nLinux\n"
    assert r.stderr == b""


def test_failure_separates_stderr():
    r = _run(2, b"", b"ls: cannot access '/nope': No such file or directory\n")
    assert r.exit_code == 2
    assert r.stdout == b""
    assert b"No such file" in r.stderr


def test_nonzero_exit_code():
    r = _run(1, b"", b"")
    assert r.exit_code == 1
    assert r.stdout == b"" and r.stderr == b""


def test_binary_safe_roundtrip():
    payload = b"\x00\x01\x02ABC\xff\xfe"
    r = _run(0, payload, b"")
    assert r.stdout == payload


def test_both_streams_populated():
    r = _run(3, b"out-data", b"err-data")
    assert r.exit_code == 3
    assert r.stdout == b"out-data"
    assert r.stderr == b"err-data"
