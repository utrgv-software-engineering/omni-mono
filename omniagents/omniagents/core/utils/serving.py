"""Start uvicorn servers on sockets they own, and know when they are up.

Choosing a port by binding a probe socket, closing it, and handing the
number to uvicorn leaves a window in which another process can take the
port; the server then fails to bind (in a background thread, silently)
while a connect-probe happily reaches whoever won the port. Here the
socket is bound once, by us, and handed to uvicorn: the port is ours from
the moment ``bind`` returns, and the real port is read back from it.
Readiness is uvicorn's own ``started`` flag, signalled when startup
finishes, rather than a sleep-and-connect loop.
"""

from __future__ import annotations

import os
import socket
import threading
from typing import Callable, Optional

import uvicorn


def bind_socket(host: str, port: int = 0) -> socket.socket:
    """Bind and listen on ``host``; ``port=0`` lets the kernel choose.

    Raises ``OSError`` when the address is taken. The bound port is
    ``sock.getsockname()[1]``. The socket is listening on return: with
    ``SO_REUSEADDR`` a merely bound socket does not stop another from
    binding the same port, a listening one does.
    """
    family, type_, proto, _, addr = socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE
    )[0]
    sock = socket.socket(family, type_, proto)
    try:
        # Lets the port be rebound while old connections sit in TIME_WAIT,
        # as uvicorn's own binding does. On Windows the option lets another
        # socket steal a port in use, so it is left off there.
        if os.name != "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(addr)
        sock.listen(2048)  # uvicorn's default backlog
    except BaseException:
        sock.close()
        raise
    return sock


def bind_first_free(host: str, start_port: int, attempts: int = 20) -> socket.socket:
    """Bind the first free port in ``start_port .. start_port + attempts - 1``.

    For servers that prefer a stable, familiar port. The returned socket
    holds the port, so nothing can take it before the server starts.
    """
    for port in range(start_port, start_port + attempts):
        try:
            return bind_socket(host, port)
        except OSError:
            continue
    raise RuntimeError(
        f"Could not find available port in range {start_port}-{start_port + attempts - 1}"
    )


class ReadyServer(uvicorn.Server):
    """A ``uvicorn.Server`` that signals when its startup has finished.

    ``ready`` is set once startup ends, whether or not it succeeded;
    ``started`` tells which. ``on_started`` runs on the server's event loop
    right after a successful startup, before any request is served.
    """

    def __init__(
        self,
        config: uvicorn.Config,
        on_started: Optional[Callable[["ReadyServer"], None]] = None,
    ) -> None:
        super().__init__(config)
        self.ready = threading.Event()
        self._on_started = on_started

    async def startup(self, sockets: Optional[list[socket.socket]] = None) -> None:
        try:
            await super().startup(sockets=sockets)
            if self.started and self._on_started is not None:
                self._on_started(self)
        finally:
            self.ready.set()


class BackgroundServer:
    """Run a uvicorn app on a daemon thread, on a socket bound up front.

    ``start()`` returns once the server accepts connections, or raises
    ``RuntimeError`` if it failed to start. ``stop()`` shuts it down and
    releases the port.
    """

    def __init__(self, config: uvicorn.Config, sock: socket.socket) -> None:
        self.server = ReadyServer(config)
        self.socket = sock
        self.port: int = sock.getsockname()[1]
        self._error: Optional[BaseException] = None
        self._thread = threading.Thread(
            target=self._run, name=f"uvicorn:{self.port}", daemon=True
        )

    def _run(self) -> None:
        try:
            self.server.run(sockets=[self.socket])
        except BaseException as exc:  # SystemExit from a failed startup, too
            self._error = exc
        finally:
            self.socket.close()
            self.server.ready.set()

    @property
    def thread(self) -> threading.Thread:
        return self._thread

    def start(self, timeout: float = 60.0) -> "BackgroundServer":
        self._thread.start()
        if not self.server.ready.wait(timeout):
            self.stop()
            raise RuntimeError(
                f"Server on port {self.port} did not start within {timeout:g}s"
            )
        if not self.server.started:
            detail = f": {self._error!r}" if self._error is not None else ""
            raise RuntimeError(f"Server on port {self.port} failed to start{detail}")
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self.server.should_exit = True
        if self._thread.is_alive():
            self._thread.join(timeout)
        if self._thread.is_alive():
            self.server.force_exit = True
            self._thread.join(timeout)
