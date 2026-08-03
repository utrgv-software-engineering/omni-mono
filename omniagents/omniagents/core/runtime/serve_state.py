"""Temporary composition state for the legacy per-process serve lifecycle.

Execution placement does not live here. Workspaces, sources, filesystem and
process adapters, sandbox sessions, and lifecycle controllers are owned by an
AgentHost and resolved through immutable environment leases.

``SwitchContext`` remains only while ``omni serve`` owns one profile-switch
orchestrator per process. The launcher AgentHostManager cutover will move this
composition object behind the environment lifecycle manager and delete this
module.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = [
    "SwitchContext",
    "set_switch_context",
    "get_switch_context",
    "reset",
]


@dataclass
class SwitchContext:
    """Orchestration state for one legacy serve process's environment.

    The environment lifecycle controller owns access to this object for RPCs
    and agent tools. The process-global reference is used only by serve startup,
    shutdown, and the temporary default entry point for ``perform_switch``.
    """

    app: Any
    spec: Any
    session_id: str
    environment_id: str
    workspace_id: str
    snapshot_dir: Optional[Any]
    sources_parsed: List[Dict[str, Any]]
    credentials_parsed: List[Dict[str, Any]]
    workspace_dir: Any
    project_id: Optional[str]
    host: str
    port: int
    auth_token: Optional[str]
    profile_resolver: Optional[Any] = None

    current_profile_path: Optional[str] = None
    current_profile_name: Optional[str] = None
    backend: Optional[str] = None
    container_id: Optional[str] = None
    service_urls: Dict[str, Any] = field(default_factory=dict)
    started_services: List[Any] = field(default_factory=list)
    discard_snapshot_on_exit: bool = False

    switching: bool = False
    last_error: Optional[str] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ready_event: asyncio.Event = field(default_factory=asyncio.Event)


_switch_context: Optional[SwitchContext] = None


def set_switch_context(ctx: Optional[SwitchContext]) -> None:
    """Publish or clear the serve process's switch orchestrator."""
    global _switch_context
    _switch_context = ctx


def get_switch_context() -> Optional[SwitchContext]:
    """Return the serve process's switch orchestrator, if installed."""
    return _switch_context


def reset() -> None:
    """Clear temporary composition state (primarily for test isolation)."""
    set_switch_context(None)
