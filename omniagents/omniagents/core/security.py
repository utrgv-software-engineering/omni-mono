"""Project-level security and compliance configuration.

All fields default to the current open-source behavior (no restrictions,
no audit, no encryption).  Institutional deployments override via the
``security:`` section in ``project.yml``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AuditConfig:
    """Controls structured audit logging."""

    enabled: bool = False
    backend: str = "file"  # "file" | "none"  (Phase 2 adds "remote")
    path: str = ""  # empty = default (~/.omniagents/audit/{project}.jsonl)
    retention_days: int = 0  # 0 = no purge
    max_detail_chars: int = (
        4096  # truncation limit for tool input/output in audit events
    )

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AuditConfig":
        return cls(
            enabled=bool(d.get("enabled", False)),
            backend=str(d.get("backend", "file")),
            path=str(d.get("path", "")),
            retention_days=int(d.get("retention_days", 0)),
            max_detail_chars=int(d.get("max_detail_chars", 4096)),
        )


@dataclass
class SessionSecurityConfig:
    """Controls session storage behaviour."""

    retention_days: int = 0  # 0 = no purge

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SessionSecurityConfig":
        return cls(
            retention_days=int(d.get("retention_days", 0)),
        )


@dataclass
class SecurityConfig:
    """Project-level security policy parsed from ``project.yml``.

    Every field defaults to the current open-source behaviour so that
    existing projects work without any ``security:`` section.
    """

    # --- Safety mode ---
    # "recommended" = respect per-agent config (current behaviour)
    # "enforced"    = force use_safe_agent=True, skip_approvals=False on every agent
    # "off"         = explicitly disable SafeAgent wrapping
    safety_mode: str = "recommended"

    # --- Tool restrictions ---
    blocked_tools: List[str] = field(default_factory=list)
    blocked_tool_patterns: List[str] = field(default_factory=list)

    # --- MCP governance ---
    mcp_require_allowlist: bool = False
    mcp_allowed_servers: List[str] = field(default_factory=list)

    # --- Per-tool timeouts (seconds) ---
    # Keys are tool names; "default" key sets the fallback.
    tool_timeouts: Dict[str, int] = field(default_factory=dict)

    # --- Audit ---
    audit: AuditConfig = field(default_factory=AuditConfig)

    # --- Session security ---
    sessions: SessionSecurityConfig = field(default_factory=SessionSecurityConfig)

    # --- Future: provider overrides (Phase 2) ---
    providers: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict (useful for threading through layers)."""
        return {
            "safety_mode": self.safety_mode,
            "blocked_tools": self.blocked_tools,
            "blocked_tool_patterns": self.blocked_tool_patterns,
            "mcp_require_allowlist": self.mcp_require_allowlist,
            "mcp_allowed_servers": self.mcp_allowed_servers,
            "tool_timeouts": self.tool_timeouts,
            "audit": {
                "enabled": self.audit.enabled,
                "backend": self.audit.backend,
                "path": self.audit.path,
                "retention_days": self.audit.retention_days,
                "max_detail_chars": self.audit.max_detail_chars,
            },
            "sessions": {
                "retention_days": self.sessions.retention_days,
            },
            "providers": self.providers,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "SecurityConfig":
        """Parse the ``security:`` section of ``project.yml``.

        Accepts *None* or an empty dict and returns safe defaults.
        """
        if not d:
            return cls()

        valid_modes = ("recommended", "enforced", "off")
        mode = str(d.get("safety_mode", "recommended")).lower()
        if mode not in valid_modes:
            print(
                f"Warning: security.safety_mode '{mode}' not recognised. "
                f"Valid values: {valid_modes}. Using 'recommended'."
            )
            mode = "recommended"

        return cls(
            safety_mode=mode,
            blocked_tools=_str_list(d.get("blocked_tools")),
            blocked_tool_patterns=_str_list(d.get("blocked_tool_patterns")),
            mcp_require_allowlist=bool(d.get("mcp_require_allowlist", False)),
            mcp_allowed_servers=_str_list(d.get("mcp_allowed_servers")),
            tool_timeouts=_int_dict(d.get("tool_timeouts")),
            audit=AuditConfig.from_dict(d.get("audit") or {}),
            sessions=SessionSecurityConfig.from_dict(d.get("sessions") or {}),
            providers=d.get("providers") or {},
        )


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _str_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [str(x) for x in v]
    return []


def _int_dict(v: Any) -> Dict[str, int]:
    if not isinstance(v, dict):
        return {}
    out: Dict[str, int] = {}
    for k, val in v.items():
        try:
            out[str(k)] = int(val)
        except (TypeError, ValueError):
            print(f"Warning: security.tool_timeouts.{k} must be an integer, skipping.")
    return out
