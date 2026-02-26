"""Domain allowlist/denylist policy enforcement."""

import fnmatch
import ipaddress
import socket
from typing import Optional


class DomainPolicy:
    """Evaluate whether a destination host is allowed by the configured policy.

    Evaluation order:
    1. Denylist (explicit block)
    2. Private IP check (block unless allow_private_ips)
    3. Allowlist (explicit allow)
    4. Default: block when allowlist is non-empty, allow when empty

    Glob semantics:
    - ``*.example.com``  → subdomains only (not apex)
    - ``**.example.com`` → apex + subdomains
    - ``*``              → everything
    """

    def __init__(
        self,
        allowlist: list[str],
        denylist: Optional[list[str]] = None,
        allow_private_ips: bool = False,
    ) -> None:
        self._allowlist = allowlist
        self._denylist = denylist or []
        self._allow_private_ips = allow_private_ips

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_allowed(self, host: str, port: int = 443) -> bool:
        """Return True if traffic to *host*:*port* should be permitted."""
        host = host.lower().strip().rstrip(".")

        # Always allow loopback — proxy itself and local services.
        if host in ("localhost", "127.0.0.1", "::1"):
            return True

        # 1. Denylist check
        if self._matches_any(host, self._denylist):
            return False

        # 2. Private IP check
        if not self._allow_private_ips and self._is_private_ip(host):
            return False

        # 3. Allowlist check
        if self._allowlist:
            return self._matches_any(host, self._allowlist)

        # 4. Empty allowlist = block everything (fail-closed when proxy enabled)
        return False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _matches_any(host: str, patterns: list[str]) -> bool:
        for pat in patterns:
            pat = pat.lower().strip()
            if pat == "*":
                return True
            if pat.startswith("**."):
                # Match apex AND subdomains: **.example.com
                base = pat[3:]  # "example.com"
                if host == base or host.endswith("." + base):
                    return True
            elif pat.startswith("*."):
                # Match subdomains only: *.example.com → ?*.example.com
                base = pat[1:]  # ".example.com"
                if fnmatch.fnmatch(host, "?*" + base):
                    return True
            else:
                # Exact match or general glob
                if fnmatch.fnmatch(host, pat):
                    return True
        return False

    @staticmethod
    def _is_private_ip(host: str) -> bool:
        """Check if host is a private/reserved IP address."""
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            # Not an IP literal — try resolving. If resolution fails,
            # treat as non-private (it will fail at connect time anyway).
            try:
                info = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
                if info:
                    addr = ipaddress.ip_address(info[0][4][0])
                else:
                    return False
            except (socket.gaierror, OSError):
                return False

        return (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_reserved
        )
