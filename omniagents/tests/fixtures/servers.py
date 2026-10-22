"""Real uvicorn servers for tests, on ports the server itself holds.

The socket is bound (port 0) before the server starts and handed to it,
so the port a test dials is the one its server listens on.
"""

from __future__ import annotations

import asyncio

import uvicorn

from omniagents.core.utils.serving import ReadyServer, bind_socket


async def start_server(
    app, *, log_level: str = "warning"
) -> tuple[uvicorn.Server, "asyncio.Task[None]", int]:
    """Start ``app`` on the running loop; returns ``(server, task, port)``.

    Stop it with ``server.should_exit = True`` and ``await task``.
    """
    sock = bind_socket("127.0.0.1", 0)
    port = sock.getsockname()[1]
    started = asyncio.Event()
    server = ReadyServer(
        uvicorn.Config(app, log_level=log_level),
        on_started=lambda _server: started.set(),
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    waiter = asyncio.create_task(started.wait())
    await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    if not started.is_set():
        waiter.cancel()
        task.result()  # raises the startup failure
        raise RuntimeError("test server exited before starting")
    return server, task, port
