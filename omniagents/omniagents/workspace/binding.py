"""Auto-bind the workspace contextvar to a sandbox session.

When a ``SandboxAgent`` runs, the SDK runtime clones each capability and
calls ``cap.bind(session)`` before invoking tools. We use that hook to
swap the omniagents workspace contextvar to a ``SandboxWorkspace`` that
proxies through the bound session.

The capability is a side-effect-only one — its ``tools()`` returns ``[]``,
its ``instructions()`` returns ``None``. The agent builder auto-prepends
it to every SandboxAgent's capability list so users don't have to wire it
manually.
"""

from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import Field

from agents.sandbox.capabilities.capability import Capability

from ._runtime import set_workspace
from .sandbox import SandboxWorkspace


class WorkspaceBindingCapability(Capability):
    """Side-effect capability that wires the omniagents workspace contextvar
    to the SDK sandbox session.

    Lifecycle: the SDK runtime clones each capability per-run and calls
    ``bind(session)``. We override ``bind`` to set the workspace contextvar
    in the run's task context so any tool body that calls
    ``omniagents.workspace.read_text(...)`` etc. routes through the
    sandbox session for the duration of that task.

    No reset is needed: contextvars are scoped to the asyncio task, and
    each run lives in its own task.
    """

    type: Literal["_workspace_binding"] = "_workspace_binding"

    workspace_root: str = Field(default="/workspace", exclude=True)
    """Cached fallback root used if the bound session doesn't expose a
    manifest. Most sessions do."""

    run_as: Optional[str] = Field(default=None, exclude=True)
    """Default user identity for tool exec/spawn/read/write calls that
    don't pass an explicit ``user=``. Wired from ``AgentSpec.sandbox_run_as``
    by the builder. When ``None``, calls run as the SDK default — which is
    root inside docker containers, letting agent code subvert
    in-container security (e.g. ``iptables -F``). Set to the remapped
    non-privileged user (``"user"`` or ``"1000:1000"``) for defense in
    depth."""

    def bind(self, session: Any) -> None:
        super().bind(session)
        # Prefer the session's manifest root (always set in well-formed
        # sandbox configs); fall back to the configured default. We don't
        # store the contextvar token because we never reset — task scope
        # handles cleanup.
        root: Optional[str] = self.workspace_root
        try:
            manifest_root = session.state.manifest.root
            if isinstance(manifest_root, str) and manifest_root:
                root = manifest_root
        except Exception:
            # Session may not yet be started, or have no manifest in
            # exotic test contexts. Fall back to the configured default.
            pass
        set_workspace(
            SandboxWorkspace(
                session=session,
                root=root or "/workspace",
                default_user=self.run_as,
            )
        )

    def tools(self) -> List[Any]:
        return []
