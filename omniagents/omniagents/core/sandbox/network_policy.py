"""In-container network egress policy for docker-backed sandboxes.

Two independent iptables layers, each isolated in its own chain so they
compose without clobbering each other:

* ``OMNI_EGRESS`` — the **baseline allowlist**, applied at every bring-up
  from the profile's resolved ``OMNI_SANDBOX_NETWORK_ALLOWLIST`` manifest
  env (populated from the user's ``network.json`` by
  :mod:`omniagents.core.sandbox.profiles`). Historically this was the
  image entrypoint's ``apply-network-isolation.sh``, but the SDK creates
  containers with ``entrypoint: ["tail"]`` so the image script never ran;
  serve now owns the application. Semantics match the historical script:
  loopback, established/related, DNS, the docker host gateway, and the
  allowlist entries pass; everything else is rejected.

* ``OMNI_BLOCK`` — the **offline toggle** (``sandbox.set_network``),
  inserted at the top of OUTPUT so it precedes the baseline. Loopback,
  the host gateway (launcher MCP servers), and reply traffic for
  *inbound-originated* connections (code-server, VNC — matched via
  conntrack ``--ctdir REPLY``) still pass; every agent-originated packet
  is rejected, including packets on connections opened before the block.
  A marker file in the container's writable layer records the desired
  state so it survives serve restarts and container stop/start (the
  network namespace — and thus the rules — does not; bring-up re-applies
  from the marker).

Every helper execs inside the container **as root** via ``session.exec``.
The agent's tools run as the profile's unprivileged ``run_as`` identity
without ``CAP_NET_ADMIN``, so they cannot undo either chain. Callers gate
on the docker backend; the devbox profile grants ``NET_ADMIN`` +
``seccomp=unconfined``, which iptables-nft needs.
"""

from __future__ import annotations

import shlex
from typing import Any, Optional

BASELINE_CHAIN = "OMNI_EGRESS"
BLOCK_CHAIN = "OMNI_BLOCK"

#: Marker in the container's writable layer (durable across stop/start —
#: deliberately not under /run, which is commonly tmpfs).
BLOCK_MARKER_PATH = "/var/lib/omni/network-blocked"

_HOST_GATEWAY_SNIPPET = """\
host_gw="$(getent ahosts host.docker.internal 2>/dev/null | awk '{print $1}' | head -n1)" || true
if [ -n "${host_gw}" ]; then
  iptables -A {chain} -d "${host_gw}" -j ACCEPT
fi
"""


def _host_gateway_rules(chain: str) -> str:
    return _HOST_GATEWAY_SNIPPET.replace("{chain}", chain)


def build_baseline_script(allowlist_csv: str) -> str:
    """Bash that (re)builds the baseline allowlist chain, idempotently.

    Structural iptables commands run under ``set -e`` and fail the
    bring-up (isolation fails closed). Individual allowlist entries keep
    the historical tolerance: an unresolvable hostname warns and is
    skipped rather than aborting the launch.
    """
    entries = [e.strip() for e in allowlist_csv.split(",") if e.strip()]
    entry_lines = []
    for entry in entries:
        quoted = shlex.quote(entry)
        if "/" in entry:
            entry_lines.append(f"iptables -A {BASELINE_CHAIN} -d {quoted} -j ACCEPT")
        else:
            entry_lines.append(
                f"""resolved="$(getent ahosts {quoted} 2>/dev/null | awk '{{print $1}}' | sort -u)" || true
if [ -n "${{resolved}}" ]; then
  for ip in ${{resolved}}; do iptables -A {BASELINE_CHAIN} -d "${{ip}}" -j ACCEPT; done
else
  iptables -A {BASELINE_CHAIN} -d {quoted} -j ACCEPT 2>/dev/null || echo "WARNING: could not resolve or add allowlist entry: {entry}" >&2
fi"""
            )
    entry_block = "\n".join(entry_lines)
    return f"""set -e
iptables -N {BASELINE_CHAIN} 2>/dev/null || true
iptables -F {BASELINE_CHAIN}
iptables -A {BASELINE_CHAIN} -o lo -j ACCEPT
iptables -A {BASELINE_CHAIN} -m state --state ESTABLISHED,RELATED -j ACCEPT
iptables -A {BASELINE_CHAIN} -p udp --dport 53 -j ACCEPT
iptables -A {BASELINE_CHAIN} -p tcp --dport 53 -j ACCEPT
{_host_gateway_rules(BASELINE_CHAIN)}
{entry_block}
iptables -A {BASELINE_CHAIN} -j REJECT --reject-with icmp-net-prohibited
iptables -C OUTPUT -j {BASELINE_CHAIN} 2>/dev/null || iptables -A OUTPUT -j {BASELINE_CHAIN}
"""


def build_block_script() -> str:
    """Bash that installs the offline block chain and its durable marker.

    Reply-direction conntrack keeps *inbound-originated* connections
    (code-server, VNC, terminals riding the serve WS) answering while
    killing agent-originated flows even if they were established before
    the block. Falls back to a plain ESTABLISHED accept when the
    conntrack match is unavailable in the image.
    """
    return f"""set -e
mkdir -p {shlex.quote(BLOCK_MARKER_PATH.rsplit("/", 1)[0])}
iptables -N {BLOCK_CHAIN} 2>/dev/null || true
iptables -F {BLOCK_CHAIN}
iptables -A {BLOCK_CHAIN} -o lo -j ACCEPT
iptables -A {BLOCK_CHAIN} -m conntrack --ctstate ESTABLISHED,RELATED --ctdir REPLY -j ACCEPT 2>/dev/null || iptables -A {BLOCK_CHAIN} -m state --state ESTABLISHED,RELATED -j ACCEPT
{_host_gateway_rules(BLOCK_CHAIN)}
iptables -A {BLOCK_CHAIN} -j REJECT --reject-with icmp-net-prohibited
iptables -C OUTPUT -j {BLOCK_CHAIN} 2>/dev/null || iptables -I OUTPUT 1 -j {BLOCK_CHAIN}
touch {shlex.quote(BLOCK_MARKER_PATH)}
"""


def build_unblock_script() -> str:
    """Bash that removes the offline block, restoring the baseline (if any)."""
    return f"""set -e
iptables -D OUTPUT -j {BLOCK_CHAIN} 2>/dev/null || true
iptables -F {BLOCK_CHAIN} 2>/dev/null || true
rm -f {shlex.quote(BLOCK_MARKER_PATH)}
"""


async def _exec_root(session: Any, script: str, *, what: str) -> None:
    result = await session.exec(script, shell=True, user="root")
    if result.exit_code != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"{what} failed (exit {result.exit_code}): {stderr or '(empty stderr)'}"
        )


async def apply_baseline_egress(session: Any, allowlist_csv: Optional[str]) -> None:
    """(Re)apply the baseline allowlist chain; no-op when no allowlist.

    Idempotent — safe on fresh containers, resumed containers whose rules
    survived, and restarted containers whose network namespace was
    recreated. Raises on failure so bring-up fails closed.
    """
    if not allowlist_csv or not allowlist_csv.strip():
        return
    await _exec_root(
        session, build_baseline_script(allowlist_csv), what="network allowlist"
    )


async def set_network_blocked(session: Any, blocked: bool) -> None:
    """Install or remove the offline block chain (and its marker)."""
    script = build_block_script() if blocked else build_unblock_script()
    what = "network block" if blocked else "network unblock"
    await _exec_root(session, script, what=what)


async def network_blocked(session: Any) -> bool:
    """True when the durable offline marker is present. Errors read as False."""
    try:
        result = await session.exec(
            f"test -f {shlex.quote(BLOCK_MARKER_PATH)}", shell=True, user="root"
        )
    except Exception:
        return False
    return result.exit_code == 0
