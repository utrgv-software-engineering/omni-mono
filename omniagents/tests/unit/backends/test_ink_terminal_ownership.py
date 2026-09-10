"""The Ink backend gives the terminal up once the TUI holds it.

Python keeps fd 0/1/2 on the tty after spawning node unless it lets go
at the descriptor level; every tool command would then inherit them,
steal keystrokes, or reset the tty to cooked mode on exit. These tests
pin the two guarantees: ``detach_terminal`` really moves the
descriptors (and puts them back), and a ``HostWorkspace.exec`` command
never sees the caller's stdin.
"""

import os
import subprocess
import sys
import textwrap

import pytest

from omniagents.workspace import HostWorkspace


def test_detach_terminal_moves_fds_and_restores(tmp_path):
    log = tmp_path / "log.txt"
    script = textwrap.dedent(
        f"""
        import os, sys
        from omniagents.backends.ink import detach_terminal
        log_fd = os.open({str(log)!r}, os.O_WRONLY | os.O_CREAT)
        os.write(1, b"before\\n")
        restore = detach_terminal(log_fd)
        os.write(1, b"hidden-out\\n")
        os.write(2, b"hidden-err\\n")
        got = os.read(0, 10)
        assert got == b"", got
        restore()
        os.write(1, b"after\\n")
        os.write(2, b"after-err\\n")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        input=b"typed\n",
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b"before\nafter\n"
    assert result.stderr == b"after-err\n"
    assert log.read_text() == "hidden-out\nhidden-err\n"


@pytest.mark.asyncio
async def test_host_exec_never_inherits_stdin(tmp_path):
    workspace = HostWorkspace(root=str(tmp_path))
    r_fd, w_fd = os.pipe()
    saved = os.dup(0)
    try:
        os.dup2(r_fd, 0)
        os.write(w_fd, b"from-the-terminal\n")
        result = await workspace.exec(
            [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"],
            shell=False,
            timeout=30,
        )
    finally:
        os.dup2(saved, 0)
        os.close(saved)
        os.close(r_fd)
        os.close(w_fd)
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "''"
