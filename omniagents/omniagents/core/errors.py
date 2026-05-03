"""Framework-level exceptions shared across core modules and builtin tools."""

from __future__ import annotations


class WorkspaceRootRequiredError(RuntimeError):
    """Raised when a run or tool needs ``workspace_root`` and no value is set.

    The framework no longer falls back to ``os.getcwd()``. The backend that
    constructs ``AgentService`` is expected to either:

      * pass ``default_workspace_root=os.getcwd()`` (CLI-style backends like
        ``ink``/``web`` that auto-launch in the user's terminal), or
      * leave the default unset and rely on the client to populate
        ``workspace_root`` via ``session.ensure`` / ``start_run``
        (production server deployments).

    Falling back to the server process's cwd silently anchored sub-agents
    and tool subprocesses to the wrong directory; this error makes the
    misconfiguration loud at the boundary instead of corrupt state on disk.
    """

    def __init__(self, where: str = "start_run") -> None:
        super().__init__(
            f"workspace_root is required but not set ({where}). "
            "The client must populate it via session.ensure / start_run "
            "context, or the backend must construct AgentService with "
            "default_workspace_root=<path>."
        )
        self.where = where
