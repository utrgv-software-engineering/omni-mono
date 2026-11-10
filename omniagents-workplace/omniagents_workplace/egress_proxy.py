"""The egress proxy: the only way out for what factories run.

An allowlisting forward proxy (``omniagents-workplace egress-proxy``). Clients
use it as their ``HTTP_PROXY``/``HTTPS_PROXY``: HTTPS through ``CONNECT``, plain
HTTP by absolute URL. Each factory authenticates with its own credentials
(``Proxy-Authorization: Basic factory:token``), so the proxy knows whose
allowlist applies: the factory's own when it has one (Confidential
factories), else the deployment's.

The configuration (JSON, written by Workplace, reread when it changes)::

    {"deployment": ["api.openai.com", "*.openai.azure.com", "pypi.org:443"],
     "factories": {"<id>": {"token": "...", "name": "...", "allow": [...] | null}}}

An entry is a host, ``*.domain`` (any subdomain), or either with ``:port``;
without a port, 80 and 443. Every denial is logged as JSON to stdout and
appended to ``denials`` (JSONL) for Workplace to audit. Nothing about a request
beyond its destination is logged.

Network rules make the proxy the only route out (internal Docker networks,
the host firewall, QuickSand's ``mounts_only``); the proxy decides where.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import time
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlsplit

logger = logging.getLogger("omniagents_workplace.egress")

DEFAULT_PORTS = (80, 443)
MAX_HEADER = 65536


def allowed(host: str, port: int, entries) -> bool:
    host = host.lower().rstrip(".")
    for entry in entries or ():
        name, _, wanted = str(entry).lower().strip().rpartition(":")
        if not name or not wanted.isdigit():
            name, wanted = str(entry).lower().strip(), ""
        ports = (int(wanted),) if wanted else DEFAULT_PORTS
        if port not in ports:
            continue
        if name.startswith("*."):
            if host.endswith(name[1:]) and host != name[2:]:
                return True
        elif host == name:
            return True
    return False


class EgressProxy:
    def __init__(self, config_path: Path, denials_path: Optional[Path] = None) -> None:
        self.config_path = Path(config_path)
        self.denials_path = Path(denials_path) if denials_path else None
        self._config: dict = {}
        self._mtime = None

    def config(self) -> dict:
        try:
            mtime = self.config_path.stat().st_mtime_ns
        except OSError:
            return {}  # no configuration: nobody is allowed out
        if mtime != self._mtime:
            try:
                self._config = json.loads(self.config_path.read_text())
                self._mtime = mtime
            except (OSError, ValueError):
                logger.exception("egress: configuration unreadable; keeping the last")
        return self._config

    def factory(self, header: Optional[str]) -> Optional[Tuple[str, dict]]:
        if not header or not header.lower().startswith("basic "):
            return None
        try:
            user, _, token = base64.b64decode(header[6:]).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            return None
        entry = (self.config().get("factories") or {}).get(user)
        if entry and hmac.compare_digest(str(entry.get("token", "")), token):
            return user, entry
        return None

    def permits(self, factory: dict, host: str, port: int) -> bool:
        entries = factory.get("allow")
        if entries is None:
            entries = self.config().get("deployment") or []
        return allowed(host, port, entries)

    def deny(
        self, factory_id: Optional[str], name: str, host: str, port: int, why: str
    ) -> None:
        record = {
            "at": time.time(),
            "factory": factory_id,
            "factory_name": name,
            "host": host,
            "port": port,
            "reason": why,
        }
        logger.warning("%s", json.dumps({"event": "egress.denied", **record}))
        if self.denials_path:
            try:
                with self.denials_path.open("a") as out:
                    out.write(json.dumps(record) + "\n")
            except OSError:
                logger.exception("egress: denial not recorded")

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
        except (
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            asyncio.TimeoutError,
        ):
            writer.close()
            return
        try:
            lines = head.decode("latin-1").split("\r\n")
            method, target, version = lines[0].split(" ", 2)
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    key, _, value = line.partition(":")
                    headers[key.strip().lower()] = value.strip()
        except ValueError:
            await self.answer(writer, 400, "Bad request")
            return
        found = self.factory(headers.get("proxy-authorization"))
        if found is None:
            await self.answer(writer, 407, "Proxy authentication required", auth=True)
            return
        factory_id, factory = found
        if method == "CONNECT":
            host, _, port_text = target.rpartition(":")
            port = int(port_text) if port_text.isdigit() else 443
            host = host.strip("[]")
        else:
            url = urlsplit(target)
            if url.scheme != "http" or not url.hostname:
                await self.answer(writer, 400, "Use an absolute http:// URL or CONNECT")
                return
            host, port = url.hostname, url.port or 80
        if not self.permits(factory, host, port):
            self.deny(
                factory_id, factory.get("name", ""), host, port, "not on the allowlist"
            )
            await self.answer(
                writer, 403, f"{host}:{port} is not on this factory's allowlist"
            )
            return
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), 30
            )
        except (OSError, asyncio.TimeoutError):
            await self.answer(writer, 502, f"Could not reach {host}:{port}")
            return
        if method == "CONNECT":
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
        else:
            # Forward the request in origin form, without the proxy's credentials.
            url = urlsplit(target)
            path = (url.path or "/") + (f"?{url.query}" if url.query else "")
            kept = [
                line
                for line in lines[1:]
                if line
                and not line.lower().startswith(
                    ("proxy-authorization:", "proxy-connection:")
                )
            ]
            upstream_writer.write(
                f"{method} {path} {version}\r\n".encode("latin-1")
                + "\r\n".join(kept).encode("latin-1")
                + b"\r\n\r\n"
            )
            await upstream_writer.drain()
        await asyncio.gather(
            self.pipe(reader, upstream_writer), self.pipe(upstream_reader, writer)
        )

    @staticmethod
    async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001 - already closed
                pass

    @staticmethod
    async def answer(writer, status: int, message: str, auth: bool = False) -> None:
        reasons = {
            400: "Bad Request",
            403: "Forbidden",
            407: "Proxy Authentication Required",
            502: "Bad Gateway",
        }
        body = message.encode()
        extra = b'Proxy-Authenticate: Basic realm="factory egress"\r\n' if auth else b""
        writer.write(
            f"HTTP/1.1 {status} {reasons.get(status, 'Error')}\r\n".encode()
            + extra
            + f"Content-Type: text/plain\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        try:
            await writer.drain()
        finally:
            writer.close()

    async def serve(self, host: str, port: int) -> None:
        server = await asyncio.start_server(self.handle, host, port, limit=MAX_HEADER)
        logger.info("egress: listening on %s:%d", host, port)
        async with server:
            await server.serve_forever()
