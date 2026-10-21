"""Framework-level exceptions shared across core modules and builtin tools."""

from __future__ import annotations


class WorkspaceRootRequiredError(RuntimeError):
    """Raised when a run or tool needs ``workspace_root`` and no value is set.

    The framework never falls back to ``os.getcwd()`` and path metadata cannot
    select a backend. A workspace-aware tool receives ``workspace_root`` only
    after ``start_run`` resolves an explicit environment lease.
    """

    def __init__(self, where: str = "start_run") -> None:
        super().__init__(
            f"workspace_root is required but not set ({where}). "
            "Select an execution environment for the run before invoking "
            "workspace-aware tools."
        )
        self.where = where
