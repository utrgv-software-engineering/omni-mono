"""Proxy lifecycle manager — daemon thread with asyncio event loop."""

import asyncio
import atexit
import logging
import socket
import threading
from dataclasses import dataclass
from typing import Optional

from .config import NetworkConfig
from .env_injector import build_proxy_env_vars, inject_into_os_environ, remove_from_os_environ
from .policy import DomainPolicy

logger = logging.getLogger(__name__)


@dataclass
class ProxyHandle:
    """Handle returned by start_proxy() for lifecycle management."""

    http_port: int
    socks5_port: int
    thread: threading.Thread
    loop: asyncio.AbstractEventLoop
    injected_keys: list[str]
    _stopped: bool = False

    def stop(self) -> None:
        """Gracefully stop the proxy and remove env vars."""
        if self._stopped:
            return
        self._stopped = True

        remove_from_os_environ(self.injected_keys)

        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)


# Module-level handle for atexit cleanup
_active_handle: Optional[ProxyHandle] = None


def _pick_port() -> int:
    """Bind an ephemeral port and return its number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_proxy(config: NetworkConfig) -> ProxyHandle:
    """Start HTTP and optional SOCKS5 proxy in a daemon thread.

    Blocks until both servers are listening, then injects env vars.
    """
    global _active_handle

    policy = DomainPolicy(
        allowlist=config.allowlist,
        denylist=config.denylist,
        allow_private_ips=config.allow_private_ips,
    )

    http_port = config.http_port or _pick_port()
    socks5_port = (config.socks5_port or _pick_port()) if config.enable_socks5 else 0

    ready = threading.Event()

    def _run_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Store loop reference on handle so stop() can access it
        handle.loop = loop

        async def _start():
            from .http_server import start_http_proxy
            http_server = await start_http_proxy(policy, "127.0.0.1", http_port)

            socks_server = None
            if socks5_port:
                from .socks5_server import start_socks5_proxy
                socks_server = await start_socks5_proxy(policy, "127.0.0.1", socks5_port)

            ready.set()

            # Serve forever
            try:
                await asyncio.gather(
                    http_server.serve_forever(),
                    socks_server.serve_forever() if socks_server else asyncio.sleep(float("inf")),
                )
            except asyncio.CancelledError:
                pass
            finally:
                http_server.close()
                await http_server.wait_closed()
                if socks_server:
                    socks_server.close()
                    await socks_server.wait_closed()

        try:
            loop.run_until_complete(_start())
        except Exception:
            logger.debug("proxy event loop exited", exc_info=True)
        finally:
            # Cancel remaining tasks before closing the loop
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    # Pre-create handle so the thread can set loop on it
    handle = ProxyHandle(
        http_port=http_port,
        socks5_port=socks5_port,
        thread=threading.Thread(target=_run_loop, daemon=True, name="proxy-loop"),
        loop=asyncio.new_event_loop(),  # placeholder, replaced in thread
        injected_keys=[],
    )

    handle.thread.start()

    if not ready.wait(timeout=10):
        raise RuntimeError("Proxy servers failed to start within 10 seconds")

    # Inject env vars
    env_vars = build_proxy_env_vars(http_port, socks5_port)
    handle.injected_keys = inject_into_os_environ(env_vars)

    _active_handle = handle
    atexit.register(handle.stop)

    return handle


def stop_proxy() -> None:
    """Stop the currently active proxy, if any."""
    global _active_handle
    if _active_handle:
        _active_handle.stop()
        _active_handle = None
