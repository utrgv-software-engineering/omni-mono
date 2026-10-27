"""The factory's agents' sessions, read in the browser: a proxy to their runtimes.

The Factory page shows a run's sessions with the shared chat UI (read-only),
which speaks the agents' runtime protocol over a WebSocket. The factory's
server serves each agent's runtime at ``/agents/<key>/`` behind its token, on
a network the browser may not reach. This proxies them for signed-in admins:
``/workplace/api/factory/runtime/agents/<key>/ws`` (and GETs beside it), with
the factory's token added here, never given to the browser.

It is for reading: the runtime's protocol can also start runs and change
things, so only the methods a transcript viewer calls go through (the
handshake, ``resume_session``, which replays a session's events,
explicitly listed transcript queries, and the server function
``workers.list``). Anything else is refused with a JSON-RPC error, and
logged: ``ack_events`` too, which compacts the session's journal under the
next reader, and ``client_functions``, as the page runs none.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional
from urllib.parse import urlsplit

__all__ = ["allowed", "relay"]

logger = logging.getLogger(__name__)

# An explicit allowlist: new RPCs must not silently inherit the factory bearer.
_EXACT = {
    "initialize",
    "initialized",
    "ping",
    "capabilities",
    "queue_status",
    "slash.list",
    "resume_session",
    "get_agent_info",
    "list_sessions",
    "get_session_history",
    "get_thread",
    "list_turns",
    "list_items",
    "get_item",
    "list_threads",
    "search_threads",
    "list_thread_descendants",
    "get_plan",
    "get_run_diff",
    "list_queue",
    "list_models",
    "get_model",
}


#: Server functions (``server_call``) that only read.
_SERVER_READS = {"workers.list"}


def allowed(method: Any, params: Any = None) -> bool:
    if method == "server_call":
        return isinstance(params, dict) and params.get("function") in _SERVER_READS
    return isinstance(method, str) and method in _EXACT


def _refusal(message: Any) -> Optional[str]:
    """The error to answer a refused request with (``None``: it may go on)."""
    if not isinstance(message, dict) or "method" not in message:
        return None  # a response to the server, or not JSON-RPC
    if allowed(message["method"], message.get("params")):
        return None
    logger.info("factory runtime: refused %s (read-only)", message["method"])
    if message.get("id") is None:
        return ""  # a notification: dropped
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": message["id"],
            "error": {
                "code": -32601,
                "message": f"{message['method']} is not for reading a session",
            },
        }
    )


async def relay(
    websocket: Any,
    upstream_url: str,
    token: Optional[str],
    authorized=lambda: True,
    policy=None,
) -> None:
    """Between the browser's ``websocket`` and the agent's runtime at ``upstream_url``."""
    import websockets

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        upstream = await websockets.connect(
            upstream_url, additional_headers=headers, max_size=None
        )
    except Exception as exc:
        logger.warning("factory runtime: %s: %s", upstream_url, exc)
        await websocket.close(code=1011)
        return

    async def browser_to_factory():
        while True:
            text = await websocket.receive_text()
            if not authorized():
                await websocket.close(code=1008)
                return
            try:
                parsed = json.loads(text)
            except ValueError:
                continue
            batch = parsed if isinstance(parsed, list) else [parsed]
            if policy is not None:
                for message in batch:
                    forward, reply = await policy(message)
                    if reply is not None:
                        await websocket.send_text(json.dumps(reply))
                    if forward is not None:
                        await upstream.send(json.dumps(forward))
                continue
            refusals = [r for r in (_refusal(m) for m in batch) if r is not None]
            if refusals:
                for refusal in refusals:
                    if refusal:
                        await websocket.send_text(refusal)
                passing = [m for m in batch if _refusal(m) is None]
                if not passing:
                    continue
                text = json.dumps(passing if isinstance(parsed, list) else passing[0])
            await upstream.send(text)

    async def factory_to_browser():
        async for message in upstream:
            await websocket.send_text(
                message if isinstance(message, str) else message.decode()
            )

    async def check_session():
        while authorized():
            await asyncio.sleep(1)
        await websocket.close(code=1008)

    tasks = [
        asyncio.create_task(browser_to_factory()),
        asyncio.create_task(factory_to_browser()),
        asyncio.create_task(check_session()),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await upstream.close()
        try:
            await websocket.close()
        except Exception:
            pass


def same_origin(origin: Optional[str], host: Optional[str]) -> bool:
    """A page of this workplace's (not another site acting with its cookie)."""
    if not origin or not host:
        return False
    return urlsplit(origin).netloc == host


def trusted_origin(origin: Optional[str], base_url: str, additional_origins=()) -> bool:
    """Compare to configured public origin, never an attacker-supplied Host."""
    if additional_origins:
        return any(
            trusted_origin(origin, configured)
            for configured in (base_url, *additional_origins)
        )
    if not origin:
        return False
    try:
        source, target = urlsplit(origin), urlsplit(base_url)

        def identity(url):
            return (
                url.scheme,
                url.hostname,
                url.port or (443 if url.scheme == "https" else 80),
            )

        return (
            source.scheme in ("http", "https")
            and not source.username
            and not source.password
            and identity(source) == identity(target)
        )
    except ValueError:
        return False
