"""Asyncio SOCKS5 proxy (RFC 1928) with domain policy enforcement.

Only supports TCP CONNECT (CMD 0x01) with no-auth (METHOD 0x00).
UDP ASSOCIATE replies with "command not supported".
"""

import asyncio
import logging
import struct

from .policy import DomainPolicy

logger = logging.getLogger(__name__)

_RELAY_BUF = 65536
_CONNECT_TIMEOUT = 15

# SOCKS5 constants
_VER = 0x05
_CMD_CONNECT = 0x01
_ATYP_IPV4 = 0x01
_ATYP_DOMAIN = 0x03
_ATYP_IPV6 = 0x04

# Reply codes
_REP_SUCCESS = 0x00
_REP_GENERAL_FAILURE = 0x01
_REP_NOT_ALLOWED = 0x02
_REP_NET_UNREACHABLE = 0x03
_REP_CONN_REFUSED = 0x05
_REP_CMD_NOT_SUPPORTED = 0x07


async def start_socks5_proxy(
    policy: DomainPolicy,
    host: str = "127.0.0.1",
    port: int = 0,
    started: asyncio.Event | None = None,
) -> asyncio.Server:
    """Start the SOCKS5 proxy server. Returns the asyncio.Server."""

    async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            await _handle_socks5(reader, writer, policy)
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        except Exception:
            logger.debug("socks5 proxy handler error", exc_info=True)
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


async def _handle_socks5(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    policy: DomainPolicy,
) -> None:
    """Full SOCKS5 handshake + CONNECT."""
    # --- Greeting ---
    ver = (await reader.readexactly(1))[0]
    if ver != _VER:
        return

    nmethods = (await reader.readexactly(1))[0]
    methods = await reader.readexactly(nmethods)

    # We only support no-auth (0x00)
    if 0x00 not in methods:
        writer.write(bytes([_VER, 0xFF]))  # no acceptable methods
        await writer.drain()
        return

    writer.write(bytes([_VER, 0x00]))  # choose no-auth
    await writer.drain()

    # --- Request ---
    header = await reader.readexactly(4)  # VER, CMD, RSV, ATYP
    cmd = header[1]
    atyp = header[3]

    if cmd != _CMD_CONNECT:
        _send_reply(writer, _REP_CMD_NOT_SUPPORTED)
        await writer.drain()
        return

    # Parse destination address
    if atyp == _ATYP_IPV4:
        raw = await reader.readexactly(4)
        dst_host = ".".join(str(b) for b in raw)
    elif atyp == _ATYP_DOMAIN:
        length = (await reader.readexactly(1))[0]
        dst_host = (await reader.readexactly(length)).decode("ascii", errors="replace")
    elif atyp == _ATYP_IPV6:
        raw = await reader.readexactly(16)
        # Format as IPv6 string
        groups = struct.unpack("!8H", raw)
        dst_host = ":".join(f"{g:x}" for g in groups)
    else:
        _send_reply(writer, _REP_GENERAL_FAILURE)
        await writer.drain()
        return

    port_bytes = await reader.readexactly(2)
    dst_port = struct.unpack("!H", port_bytes)[0]

    # --- Policy check ---
    if not policy.is_allowed(dst_host, dst_port):
        _send_reply(writer, _REP_NOT_ALLOWED)
        await writer.drain()
        return

    # --- Connect upstream ---
    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(dst_host, dst_port),
            timeout=_CONNECT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        _send_reply(writer, _REP_NET_UNREACHABLE)
        await writer.drain()
        return
    except OSError:
        _send_reply(writer, _REP_CONN_REFUSED)
        await writer.drain()
        return

    # Send success reply with bound address 0.0.0.0:0
    _send_reply(writer, _REP_SUCCESS)
    await writer.drain()

    # --- Bidirectional relay ---
    await _relay(reader, writer, upstream_reader, upstream_writer)


def _send_reply(writer: asyncio.StreamWriter, rep: int) -> None:
    """Send a SOCKS5 reply with IPv4 0.0.0.0:0 as bound address."""
    writer.write(bytes([
        _VER, rep, 0x00,
        _ATYP_IPV4, 0, 0, 0, 0,  # BND.ADDR
        0, 0,                      # BND.PORT
    ]))


async def _relay(
    reader_a: asyncio.StreamReader,
    writer_a: asyncio.StreamWriter,
    reader_b: asyncio.StreamReader,
    writer_b: asyncio.StreamWriter,
) -> None:
    """Bidirectional byte relay."""

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

    await asyncio.gather(
        _pipe(reader_a, writer_b),
        _pipe(reader_b, writer_a),
        return_exceptions=True,
    )
