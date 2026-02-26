"""Network isolation proxy for omni-code.

Public API:
    maybe_start_proxy(project_yml_path) — start proxy if config says so
    stop_proxy() — stop the proxy and remove env vars
"""

import logging
from typing import Optional

from .config import load_network_config
from .manager import ProxyHandle, start_proxy, stop_proxy

logger = logging.getLogger(__name__)


def _patch_websockets_proxy() -> None:
    """Fix websockets 15.x + uvloop crash (double-set on response future).

    The HTTPProxyConnection.run_parser / connection_lost methods don't guard
    against the response future already being done, causing InvalidStateError
    when uvloop delivers data_received and connection_lost in quick succession.
    """
    try:
        from websockets.asyncio.client import HTTPProxyConnection
    except ImportError:
        return

    _orig_run_parser = HTTPProxyConnection.run_parser
    _orig_connection_lost = HTTPProxyConnection.connection_lost

    def _safe_run_parser(self):
        if self.response.done():
            return
        _orig_run_parser(self)

    def _safe_connection_lost(self, exc):
        if self.response.done():
            return
        _orig_connection_lost(self, exc)

    HTTPProxyConnection.run_parser = _safe_run_parser
    HTTPProxyConnection.connection_lost = _safe_connection_lost


def maybe_start_proxy(project_yml_path: Optional[str] = None) -> Optional[ProxyHandle]:
    """Start the network isolation proxy if enabled in config.

    Safe to call unconditionally — returns None if proxy is disabled.
    """
    config = load_network_config(project_yml_path)

    if not config.enabled:
        return None

    _patch_websockets_proxy()
    handle = start_proxy(config)

    domains = ", ".join(config.allowlist[:5]) if config.allowlist else "(none — blocking all)"
    suffix = "..." if len(config.allowlist) > 5 else ""
    logger.info(
        "Network proxy active on :%d (HTTP) / :%d (SOCKS5) — allowlist: %s%s",
        handle.http_port,
        handle.socks5_port,
        domains,
        suffix,
    )

    return handle


__all__ = ["maybe_start_proxy", "stop_proxy"]
