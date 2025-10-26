import asyncio
import os
from types import SimpleNamespace

import pytest
import pytest_asyncio

from omniagents.rpc.terminal import (
    TerminalAuthorizationError,
    TerminalError,
    TerminalManager,
    TerminalNotFoundError,
)


class DummyProcess:
    def __init__(self):
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0
        self.returncode = 0

    def terminate(self):
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1

    async def wait(self):
        self.wait_calls += 1
        await asyncio.sleep(0)
        return self.returncode


@pytest_asyncio.fixture
async def terminal_env(monkeypatch):
    manager = TerminalManager()
    processes = []
    open_fds = []
    exec_calls = []
    winsize_calls = []

    def fake_openpty():
        read_fd, write_fd = os.pipe()
        open_fds.append((read_fd, write_fd))
        return write_fd, read_fd

    async def fake_exec(*cmd, **kwargs):
        exec_calls.append((cmd, kwargs))
        process = DummyProcess()
        processes.append(process)
        return process

    async def fake_reader_loop(self, descriptor):
        event = asyncio.Event()
        await event.wait()

    def fake_set_winsize(self, fd, cols, rows):
        winsize_calls.append((fd, cols, rows))

    monkeypatch.setattr("omniagents.rpc.terminal.pty.openpty", fake_openpty)
    monkeypatch.setattr(
        "omniagents.rpc.terminal.asyncio.create_subprocess_exec", fake_exec
    )
    monkeypatch.setattr(TerminalManager, "_reader_loop", fake_reader_loop)
    monkeypatch.setattr(TerminalManager, "_set_winsize", fake_set_winsize)

    env = SimpleNamespace(
        manager=manager,
        processes=processes,
        open_fds=open_fds,
        exec_calls=exec_calls,
        winsize_calls=winsize_calls,
    )

    try:
        yield env
    finally:
        for session_id in list(manager._terminals.keys()):
            await manager.cleanup_session(session_id)
        for read_fd, write_fd in open_fds:
            for fd in (read_fd, write_fd):
                try:
                    os.close(fd)
                except OSError:
                    pass


def get_descriptor(manager, session_id, terminal_id):
    return manager._terminals[session_id][terminal_id]


@pytest.mark.asyncio
async def test_create_terminal_populates_descriptor(terminal_env, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    env = terminal_env
    result = await env.manager.create_terminal(
        "session-1", shell="/usr/bin/fish", cwd="/tmp/work", cols=120, rows=40
    )
    assert result["shell"] == "/usr/bin/fish"
    assert result["cols"] == 120
    assert result["rows"] == 40
    assert env.exec_calls[0][0] == ("/usr/bin/fish", "-i")
    descriptor = get_descriptor(env.manager, "session-1", result["terminal_id"])
    assert descriptor.shell == "/usr/bin/fish"
    assert descriptor.cwd == "/tmp/work"
    assert descriptor.cols == 120
    assert descriptor.rows == 40
    await env.manager.close_terminal("session-1", result["terminal_id"])


@pytest.mark.asyncio
async def test_create_terminal_defaults(monkeypatch, terminal_env):
    monkeypatch.delenv("SHELL", raising=False)
    env = terminal_env
    result = await env.manager.create_terminal(
        "session-2", shell=None, cwd="   ", cols=0, rows=-5
    )
    assert result["shell"] == "/bin/bash"
    assert result["cols"] == 80
    assert result["rows"] == 24
    descriptor = get_descriptor(env.manager, "session-2", result["terminal_id"])
    assert descriptor.cwd is None
    await env.manager.close_terminal("session-2", result["terminal_id"])


@pytest.mark.asyncio
async def test_create_terminal_requires_session_id(terminal_env):
    env = terminal_env
    with pytest.raises(TerminalError):
        await env.manager.create_terminal("", shell="/bin/bash")


@pytest.mark.asyncio
async def test_authorize_enforces_token_and_single_attach(terminal_env):
    env = terminal_env
    record = await env.manager.create_terminal("session-3")
    terminal_id = record["terminal_id"]
    token = record["token"]
    with pytest.raises(TerminalAuthorizationError):
        await env.manager.authorize("session-3", terminal_id, "bad")
    descriptor = await env.manager.authorize("session-3", terminal_id, token)
    assert descriptor.consumer_attached is True
    with pytest.raises(TerminalAuthorizationError):
        await env.manager.authorize("session-3", terminal_id, token)
    await env.manager.close_terminal("session-3", terminal_id)


@pytest.mark.asyncio
async def test_authorize_unknown_terminal(terminal_env):
    env = terminal_env
    with pytest.raises(TerminalNotFoundError):
        await env.manager.authorize("missing", "t1", "tok")


@pytest.mark.asyncio
async def test_close_terminal_removes_descriptor(terminal_env):
    env = terminal_env
    record = await env.manager.create_terminal("session-4")
    terminal_id = record["terminal_id"]
    await env.manager.close_terminal("session-4", terminal_id)
    assert "session-4" not in env.manager._terminals


@pytest.mark.asyncio
async def test_cleanup_session_shuts_down_all(terminal_env):
    env = terminal_env
    first = await env.manager.create_terminal("session-5")
    second = await env.manager.create_terminal("session-5")
    await env.manager.cleanup_session("session-5")
    assert "session-5" not in env.manager._terminals
    for process in env.processes:
        assert process.terminate_calls == 1
        assert process.wait_calls >= 1


@pytest.mark.asyncio
async def test_resize_and_write_input(monkeypatch, terminal_env):
    env = terminal_env
    record = await env.manager.create_terminal("session-6")
    descriptor = await env.manager.authorize(
        "session-6", record["terminal_id"], record["token"]
    )
    writes = []

    def fake_write(fd, data):
        writes.append((fd, data))
        return len(data)

    monkeypatch.setattr("omniagents.rpc.terminal.os.write", fake_write)
    await env.manager.write_input(descriptor, b"ls\n")
    assert writes == [(descriptor.master_fd, b"ls\n")]
    baseline_calls = len(env.winsize_calls)
    await env.manager.resize(descriptor, 4, 3)
    await env.manager.resize(descriptor, 100, 40)
    first_resize_call = env.winsize_calls[baseline_calls]
    second_resize_call = env.winsize_calls[baseline_calls + 1]
    assert first_resize_call == (descriptor.master_fd, 10, 5)
    assert second_resize_call == (descriptor.master_fd, 100, 40)
    assert descriptor.cols == 100
    assert descriptor.rows == 40
    await env.manager.close_terminal("session-6", record["terminal_id"])


@pytest.mark.asyncio
async def test_read_output_queue(terminal_env):
    env = terminal_env
    record = await env.manager.create_terminal("session-7")
    descriptor = get_descriptor(env.manager, "session-7", record["terminal_id"])
    await descriptor.output_queue.put(b"hello")
    piece = await env.manager.read_output(descriptor)
    assert piece == b"hello"
    await env.manager.close_terminal("session-7", record["terminal_id"])
