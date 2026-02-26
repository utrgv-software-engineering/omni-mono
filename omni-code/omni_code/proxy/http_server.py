"""Asyncio HTTP/CONNECT proxy with domain policy enforcement."""

import asyncio
import logging

from .policy import DomainPolicy

logger = logging.getLogger(__name__)

# Generous but bounded limits
_RELAY_BUF = 65536
_CONNECT_TIMEOUT = 15
_HEADER_MAX = 65536


async def start_http_proxy(
    policy: DomainPolicy,
    host: str = "127.0.0.1",
    port: int = 0,
    started: asyncio.Event | None = None,
) -> asyncio.Server:
    """Start the HTTP/CONNECT proxy server. Returns the asyncio.Server."""

    async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            await _handle_request(reader, writer, policy)
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        except Exception:
            logger.debug("http proxy handler error", exc_info=True)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_server(_handle_client, host, port)
    if started is not None:
        started.set()
    return server


async def _handle_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    policy: DomainPolicy,
) -> None:
    """Dispatch to CONNECT tunnel or plain HTTP forward."""
    # Read the request line
    raw_line = await asyncio.wait_for(reader.readline(), timeout=30)
    if not raw_line:
        return
    request_line = raw_line.decode("latin-1", errors="replace").strip()

    parts = request_line.split()
    if len(parts) < 3:
        return

    method = parts[0].upper()

    if method == "CONNECT":
        await _handle_connect(reader, writer, policy, parts[1])
    else:
        await _handle_plain_http(reader, writer, policy, method, parts[1], parts[2], raw_line)


# ------------------------------------------------------------------
# CONNECT tunnelling (HTTPS, WSS)
# ------------------------------------------------------------------

async def _handle_connect(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    policy: DomainPolicy,
    target: str,
) -> None:
    """Handle CONNECT host:port — tunnel bytes after policy check."""
    host, port = _parse_host_port(target, default_port=443)

    # Drain remaining headers
    await _drain_headers(reader)

    if not policy.is_allowed(host, port):

        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        return


    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=_CONNECT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        writer.write(b"HTTP/1.1 504 Gateway Timeout\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        return
    except OSError:
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        return

    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await writer.drain()

    await _relay(reader, writer, upstream_reader, upstream_writer)


# ------------------------------------------------------------------
# Plain HTTP forwarding
# ------------------------------------------------------------------

async def _handle_plain_http(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    policy: DomainPolicy,
    method: str,
    url: str,
    version: str,
    raw_request_line: bytes,
) -> None:
    """Forward a plain HTTP request after policy check."""
    # Read all headers
    headers_raw = await _read_headers(reader)
    host, port = _extract_host_from_headers(headers_raw, url)

    if not host:
        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        return

    if not policy.is_allowed(host, port):

        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        return

    # Build path from absolute URL
    path = _extract_path(url)

    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=_CONNECT_TIMEOUT,
        )
    except (asyncio.TimeoutError, OSError):
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        return

    # Reconstruct request with relative path
    request_line = f"{method} {path} {version}\r\n".encode("latin-1")
    upstream_writer.write(request_line + headers_raw + b"\r\n")

    # Forward body if Content-Length present
    content_length = _get_content_length(headers_raw)
    if content_length and content_length > 0:
        body = await reader.readexactly(content_length)
        upstream_writer.write(body)
    await upstream_writer.drain()

    # Relay response back
    await _relay(upstream_reader, upstream_writer, reader, writer, one_direction=True)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

async def _relay(
    reader_a: asyncio.StreamReader,
    writer_a: asyncio.StreamWriter,
    reader_b: asyncio.StreamReader,
    writer_b: asyncio.StreamWriter,
    one_direction: bool = False,
) -> None:
    """Bidirectional byte relay between two connections."""

    async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter):
        try:
            while True:
                data = await src.read(_RELAY_BUF)
                if not data:
                    break
                dst.write(data)
                await dst.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        finally:
            try:
                dst.close()
            except Exception:
                pass

    if one_direction:
        # Just relay upstream → client
        await _pipe(reader_a, writer_a)
    else:
        await asyncio.gather(
            _pipe(reader_a, writer_b),
            _pipe(reader_b, writer_a),
            return_exceptions=True,
        )


async def _drain_headers(reader: asyncio.StreamReader) -> None:
    """Read and discard headers until blank line."""
    total = 0
    while total < _HEADER_MAX:
        line = await asyncio.wait_for(reader.readline(), timeout=30)
        total += len(line)
        if line in (b"\r\n", b"\n", b""):
            break


async def _read_headers(reader: asyncio.StreamReader) -> bytes:
    """Read headers until blank line. Returns raw header bytes (without final CRLF)."""
    buf = bytearray()
    while len(buf) < _HEADER_MAX:
        line = await asyncio.wait_for(reader.readline(), timeout=30)
        if line in (b"\r\n", b"\n", b""):
            break
        buf.extend(line)
    return bytes(buf)


def _parse_host_port(target: str, default_port: int = 443) -> tuple[str, int]:
    """Parse 'host:port' or 'host' into (host, port)."""
    if target.startswith("["):
        # IPv6: [::1]:443
        bracket_end = target.find("]")
        if bracket_end == -1:
            return target, default_port
        host = target[1:bracket_end]
        rest = target[bracket_end + 1 :]
        if rest.startswith(":"):
            try:
                return host, int(rest[1:])
            except ValueError:
                pass
        return host, default_port

    if ":" in target:
        parts = target.rsplit(":", 1)
        try:
            return parts[0], int(parts[1])
        except ValueError:
            return target, default_port
    return target, default_port


def _extract_host_from_headers(headers_raw: bytes, url: str) -> tuple[str, int]:
    """Extract host and port from Host header or absolute URL."""
    # Try Host header
    for line in headers_raw.split(b"\r\n"):
        if line.lower().startswith(b"host:"):
            host_val = line.split(b":", 1)[1].strip().decode("latin-1")
            return _parse_host_port(host_val, default_port=80)

    # Try absolute URL
    if url.startswith("http://"):
        url = url[7:]
    elif url.startswith("https://"):
        url = url[8:]
    slash = url.find("/")
    if slash != -1:
        url = url[:slash]
    return _parse_host_port(url, default_port=80)


def _extract_path(url: str) -> str:
    """Extract path from absolute URL, defaulting to /."""
    for prefix in ("http://", "https://"):
        if url.startswith(prefix):
            rest = url[len(prefix) :]
            slash = rest.find("/")
            if slash != -1:
                return rest[slash:]
            return "/"
    return url  # Already relative


def _get_content_length(headers_raw: bytes) -> int | None:
    """Extract Content-Length from raw headers."""
    for line in headers_raw.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            try:
                return int(line.split(b":", 1)[1].strip())
            except ValueError:
                pass
    return None
