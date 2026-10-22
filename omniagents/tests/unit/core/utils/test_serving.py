import socket
import threading
import urllib.request
from contextlib import asynccontextmanager

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from omniagents.core.utils.serving import (
    BackgroundServer,
    ReadyServer,
    bind_first_free,
    bind_socket,
)


def _app(tag: str) -> FastAPI:
    app = FastAPI()

    @app.get("/")
    async def root():
        return PlainTextResponse(tag)

    return app


def _config(app) -> uvicorn.Config:
    return uvicorn.Config(app, log_level="error", access_log=False)


def _get(port: int) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
        return resp.read().decode()


def _accepts(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def test_bind_socket_port_zero_holds_a_real_port():
    sock = bind_socket("127.0.0.1", 0)
    try:
        port = sock.getsockname()[1]
        assert port > 0
        # Held from bind onward: nobody else can take it.
        with pytest.raises(OSError):
            bind_socket("127.0.0.1", port)
    finally:
        sock.close()


def test_bind_first_free_skips_taken_ports():
    first = bind_socket("127.0.0.1", 0)
    try:
        port = first.getsockname()[1]
        second = bind_first_free("127.0.0.1", port, attempts=50)
        try:
            assert second.getsockname()[1] > port
        finally:
            second.close()
    finally:
        first.close()


def test_bind_first_free_gives_up_after_attempts():
    taken = bind_socket("127.0.0.1", 0)
    try:
        port = taken.getsockname()[1]
        with pytest.raises(RuntimeError, match="Could not find available port"):
            bind_first_free("127.0.0.1", port, attempts=1)
    finally:
        taken.close()


def test_background_server_serves_once_started_and_releases_port_on_stop():
    server = BackgroundServer(_config(_app("hi")), bind_socket("127.0.0.1", 0))
    server.start()
    try:
        assert server.server.started
        assert _get(server.port) == "hi"
    finally:
        server.stop()
    assert not server.thread.is_alive()
    assert not _accepts(server.port)


def test_background_server_startup_failure_raises():
    @asynccontextmanager
    async def failing(app):
        raise RuntimeError("boom")
        yield

    server = BackgroundServer(
        _config(FastAPI(lifespan=failing)), bind_socket("127.0.0.1", 0)
    )
    with pytest.raises(RuntimeError, match="failed to start"):
        server.start()
    server.thread.join(5)
    assert not _accepts(server.port)


def test_background_servers_many_sequentially_and_concurrently():
    for i in range(20):
        server = BackgroundServer(_config(_app(f"s{i}")), bind_socket("127.0.0.1", 0))
        server.start()
        try:
            assert _get(server.port) == f"s{i}"
        finally:
            server.stop()

    count = 16
    barrier = threading.Barrier(count)
    servers: dict[int, BackgroundServer] = {}
    errors = []

    def worker(i):
        try:
            barrier.wait()
            servers[i] = BackgroundServer(
                _config(_app(f"c{i}")), bind_socket("127.0.0.1", 0)
            ).start()
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    try:
        assert errors == []
        assert len({s.port for s in servers.values()}) == count
        for i, server in servers.items():
            assert _get(server.port) == f"c{i}"
    finally:
        for server in servers.values():
            server.stop()


@pytest.mark.asyncio
async def test_ready_server_on_started_runs_before_serving():
    sock = bind_socket("127.0.0.1", 0)
    port = sock.getsockname()[1]
    seen = []

    def on_started(server: ReadyServer) -> None:
        seen.append(_accepts(port))
        server.should_exit = True

    server = ReadyServer(_config(_app("x")), on_started=on_started)
    await server.serve(sockets=[sock])
    assert seen == [True]
    assert server.ready.is_set()
