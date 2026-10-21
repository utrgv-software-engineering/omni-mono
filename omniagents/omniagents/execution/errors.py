"""Typed failures raised while resolving operation execution resources."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

RPC_ERROR_EXECUTION_RESOLUTION = -32120


class ExecutionResolutionError(RuntimeError):
    """Base error with a stable machine-readable kind and structured data."""

    kind: ClassVar[str] = "execution_resolution_failed"

    def __init__(self, message: str, **data: Any) -> None:
        super().__init__(message)
        self.data = {"kind": self.kind, **data}


class EnvironmentRequiredError(ExecutionResolutionError):
    kind = "environment_required"

    def __init__(self, operation: str) -> None:
        super().__init__(
            f"{operation} requires an execution environment", operation=operation
        )


class EnvironmentNotFoundError(ExecutionResolutionError):
    kind = "environment_not_found"

    def __init__(self, environment_id: str) -> None:
        super().__init__(
            f"execution environment {environment_id!r} was not found",
            environment_id=environment_id,
        )


class EnvironmentUnavailableError(ExecutionResolutionError):
    kind = "environment_unavailable"

    def __init__(self, environment_id: str, state: str) -> None:
        super().__init__(
            f"execution environment {environment_id!r} is {state}",
            environment_id=environment_id,
            state=state,
        )


class EnvironmentProvisioningUnavailableError(ExecutionResolutionError):
    kind = "environment_provisioning_unavailable"

    def __init__(self, workspace_id: str, profile_id: str) -> None:
        super().__init__(
            "this AgentHost cannot provision execution environments",
            workspace_id=workspace_id,
            profile_id=profile_id,
        )


class EnvironmentProvisioningFailedError(ExecutionResolutionError):
    kind = "environment_provisioning_failed"

    def __init__(self, workspace_id: str, profile_id: str, reason: str) -> None:
        super().__init__(
            f"failed to provision profile {profile_id!r} for workspace "
            f"{workspace_id!r}: {reason}",
            workspace_id=workspace_id,
            profile_id=profile_id,
            reason=reason,
        )


class EnvironmentShutdownError(RuntimeError):
    """One or more environments failed to close during AgentHost shutdown."""

    def __init__(self, failures: Mapping[str, Exception]) -> None:
        self.failures = dict(failures)
        environment_ids = ", ".join(sorted(self.failures))
        super().__init__(
            f"failed to stop {len(self.failures)} execution environment(s): "
            f"{environment_ids}"
        )


class EnvironmentChangedError(ExecutionResolutionError):
    kind = "environment_changed"

    def __init__(
        self, environment_id: str, expected_generation: int, actual_generation: int
    ) -> None:
        super().__init__(
            f"execution environment {environment_id!r} changed generation",
            environment_id=environment_id,
            expected_generation=expected_generation,
            actual_generation=actual_generation,
        )


class CapabilityUnavailableError(ExecutionResolutionError):
    kind = "capability_unavailable"

    def __init__(self, environment_id: str, missing: set[str] | frozenset[str]) -> None:
        missing_names = sorted(missing)
        super().__init__(
            f"execution environment {environment_id!r} lacks required capabilities: "
            + ", ".join(missing_names),
            environment_id=environment_id,
            missing=missing_names,
        )


class WorkspaceNotFoundError(ExecutionResolutionError):
    kind = "workspace_not_found"

    def __init__(self, workspace_id: str) -> None:
        super().__init__(
            f"workspace {workspace_id!r} was not found", workspace_id=workspace_id
        )


class WorkspaceEnvironmentMismatchError(ExecutionResolutionError):
    kind = "workspace_environment_mismatch"

    def __init__(
        self,
        workspace_id: str,
        environment_id: str,
        environment_workspace_id: str,
    ) -> None:
        super().__init__(
            f"execution environment {environment_id!r} belongs to workspace "
            f"{environment_workspace_id!r}, not {workspace_id!r}",
            workspace_id=workspace_id,
            environment_id=environment_id,
            environment_workspace_id=environment_workspace_id,
        )
