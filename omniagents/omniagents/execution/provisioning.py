"""AgentHost-owned execution-environment provisioning contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from omniagents.workspace.base import Workspace

from .models import (
    EnvironmentCapability,
    EnvironmentKind,
    SandboxExecutionRuntime,
    TerminalExecutionDefaults,
    WorkspaceResource,
)


def _require_id(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class EnvironmentProvisionRequest:
    """One explicit request to materialize a Workspace through a profile."""

    environment_id: str
    workspace: WorkspaceResource
    profile_id: str

    def __post_init__(self) -> None:
        _require_id(self.environment_id, "environment_id")
        if not isinstance(self.workspace, WorkspaceResource):
            raise TypeError("workspace must be a WorkspaceResource")
        _require_id(self.profile_id, "profile_id")


@dataclass(frozen=True, slots=True)
class ProvisionedEnvironment:
    """Live adapters returned by an AgentHost environment provisioner."""

    workspace: Workspace
    kind: EnvironmentKind
    capabilities: frozenset[EnvironmentCapability]
    lifecycle_controller: Any | None = None
    sandbox_runtime: SandboxExecutionRuntime | None = None
    terminal_defaults: TerminalExecutionDefaults = field(
        default_factory=TerminalExecutionDefaults
    )

    def __post_init__(self) -> None:
        if self.workspace is None:
            raise ValueError("provisioned environment requires a Workspace adapter")
        if not isinstance(self.kind, EnvironmentKind):
            raise TypeError("kind must be an EnvironmentKind")
        if not isinstance(self.capabilities, frozenset) or not all(
            isinstance(capability, EnvironmentCapability)
            for capability in self.capabilities
        ):
            raise TypeError(
                "capabilities must be a frozenset of EnvironmentCapability values"
            )
        if self.sandbox_runtime is not None and not isinstance(
            self.sandbox_runtime, SandboxExecutionRuntime
        ):
            raise TypeError("sandbox_runtime must be a SandboxExecutionRuntime")
        if not isinstance(self.terminal_defaults, TerminalExecutionDefaults):
            raise TypeError("terminal_defaults must be TerminalExecutionDefaults")


class EnvironmentProvisioner(Protocol):
    """Product adapter used by one AgentHost to create tool runtimes."""

    async def provision(
        self, request: EnvironmentProvisionRequest
    ) -> ProvisionedEnvironment: ...
