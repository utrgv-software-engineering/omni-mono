"""Policy fetch runtime hook — pulls effective policy from omni-platform at session start.

Registered as a runtime hook on the ``"run_started"`` event. On the first
run of a session, fetches the full policy from the management plane and
applies it to the agent's security config (blocked tools, MCP allowlists,
tool timeouts, content scanning settings).

This hook bridges the management plane's entitlement model and the
agent framework's security config — governance decisions made in the
portal (entitlements, bundles, approvals) flow through to runtime
enforcement here.

Config (in project.yml)::

    security:
      providers:
        policy:
          type: "platform"
          platform_url: "${OMNI_PLATFORM_URL}"
          # Session ID and token are injected as env vars by the container
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("omniagents.policy.platform")


def _expand_env(value: str) -> str:
    if not value:
        return value
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        inner = value[2:-1]
        if ":-" in inner:
            var, default = inner.split(":-", 1)
            return os.getenv(var, default)
        return os.getenv(inner, "")
    return value


class PlatformPolicyFetcher:
    """Fetches and caches the effective policy from the management plane."""

    def __init__(
        self,
        platform_url: str,
        runtime_token: str = "",
        session_id: str = "",
    ) -> None:
        self._platform_url = platform_url.rstrip("/")
        self._runtime_token = runtime_token or os.getenv("OMNI_RUNTIME_TOKEN", "")
        self._session_id = session_id or os.getenv("OMNI_SESSION_ID", "")
        self._policy: Optional[Dict[str, Any]] = None
        self._fetched = False

    @property
    def policy(self) -> Optional[Dict[str, Any]]:
        return self._policy

    async def fetch(self) -> Dict[str, Any]:
        """Fetch policy from GET /api/v1/sessions/{session_id}/policy."""
        if self._fetched and self._policy is not None:
            return self._policy

        import requests

        url = f"{self._platform_url}/api/v1/sessions/{self._session_id}/policy"
        try:
            response = await asyncio.to_thread(
                requests.get,
                url,
                headers={
                    "Authorization": f"Bearer {self._runtime_token}",
                },
                timeout=10,
            )

            if response.ok:
                self._policy = response.json()
                self._fetched = True
                logger.info("Fetched policy for session %s", self._session_id)
                return self._policy
            else:
                logger.warning(
                    "Policy fetch returned %d: %s",
                    response.status_code,
                    response.text[:200],
                )
                return {}
        except Exception as exc:
            logger.warning("Policy fetch failed: %s", exc)
            return {}

    def apply_to_security_config(
        self, spec: Any, policy: Dict[str, Any]
    ) -> None:
        """Apply platform policy to the agent's SecurityConfig.

        Maps management plane policy fields to omniagents security config:
        - mcp_servers[].tool_blocklist → blocked_tool_patterns
        - network_allowlist → stored for proxy config
        - content_scanning → content_scanner provider config
        - session.phi_boundary → elevates safety mode
        """
        security = getattr(spec, "security_config", None) or {}

        # Apply MCP server allowlist from policy
        mcp_servers = policy.get("mcp_servers", [])
        if mcp_servers:
            allowed = [s["name"] for s in mcp_servers]
            security["mcp_require_allowlist"] = True
            security["mcp_allowed_servers"] = allowed

        # Apply tool blocklists aggregated from all MCP servers
        blocked_patterns = list(security.get("blocked_tool_patterns", []))
        for server in mcp_servers:
            for tool in server.get("tool_blocklist", []):
                pattern = f"{server['name']}.*{tool}"
                if pattern not in blocked_patterns:
                    blocked_patterns.append(pattern)
        if blocked_patterns:
            security["blocked_tool_patterns"] = blocked_patterns

        # Apply content scanning from platform policy
        scanning = policy.get("content_scanning", {})
        if scanning.get("enabled"):
            scanner_config = {
                "type": scanning.get("provider", "regex"),
            }
            # Pass custom patterns if the provider supports them
            custom_patterns = scanning.get("custom_patterns", [])
            if custom_patterns:
                scanner_config["custom_patterns"] = custom_patterns
            security.setdefault("providers", {})
            security["providers"]["content_scanner"] = scanner_config

        # Elevate safety mode in PHI contexts
        session_info = policy.get("session", {})
        if session_info.get("phi_boundary"):
            if security.get("safety_mode") != "enforced":
                security["safety_mode"] = "enforced"

        spec.security_config = security

    async def handle_run_started(
        self, *, service: Any = None, event: str = "", **kwargs: Any
    ) -> None:
        """Runtime hook — fetch policy on first run_started event."""
        if self._fetched:
            return

        policy = await self.fetch()
        if policy and service:
            spec = getattr(service, "spec", None)
            if spec:
                self.apply_to_security_config(spec, policy)
                logger.info(
                    "Applied platform policy: phi=%s, mcp_servers=%d, skills=%d",
                    policy.get("session", {}).get("phi_boundary", False),
                    len(policy.get("mcp_servers", [])),
                    len(policy.get("skills", [])),
                )


def create_policy_hook(
    policy_config: Dict[str, Any],
) -> Callable[..., Any]:
    """Create the runtime-hook callable for platform policy fetching.

    Returns a hook that fires on ``"run_started"`` events. Should be
    registered via ``service.register_runtime_hook("run_started", hook)``.
    """
    platform_url = _expand_env(
        policy_config.get("platform_url", "")
    ) or _expand_env("${OMNI_PLATFORM_URL}")
    runtime_token = _expand_env(
        policy_config.get("runtime_token", "${OMNI_RUNTIME_TOKEN}")
    )
    session_id = _expand_env(
        policy_config.get("session_id", "${OMNI_SESSION_ID}")
    )

    fetcher = PlatformPolicyFetcher(
        platform_url=platform_url,
        runtime_token=runtime_token,
        session_id=session_id,
    )
    return fetcher.handle_run_started
