"""Identity and binding models for agent tool execution.

These models intentionally contain no ``serve`` compatibility behavior.  In
particular, a missing environment never selects the host, and a path such as
``/workspace`` never identifies an execution substrate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping, TypeAlias

if TYPE_CHECKING:
    from omniagents.workspace.base import Workspace


def _require_id(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


class EnvironmentKind(str, Enum):
    """Placement family for a live tool runtime."""

    HOST = "host"
    DEVBOX = "devbox"
    REMOTE = "remote"


class EnvironmentState(str, Enum):
    """Lifecycle state of an execution environment."""

    PROVISIONING = "provisioning"
    READY = "ready"
    REPLACING = "replacing"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class EnvironmentCapability(str, Enum):
    """Backend capabilities that operations and tools may require."""

    WORKSPACE_READ = "workspace_read"
    WORKSPACE_WRITE = "workspace_write"
    PROCESS = "process"
    NETWORK = "network"
    PTY = "pty"
    HTTP = "http"
    SERVICES = "services"
    ENVIRONMENT_LIFECYCLE = "environment_lifecycle"


@dataclass(frozen=True, slots=True)
class WorkspaceResource:
    """Durable project identity, independent of a live environment.

    ``WorkspaceResource`` is distinct from
    :class:`omniagents.workspace.base.Workspace`: the former identifies
    durable project/snapshot state, while the latter is a live filesystem and
    process adapter exposed by an environment lease.
    """

    workspace_id: str
    owner_user_id: str | None = None
    snapshot_ref: str | None = None
    sources: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        _require_id(self.workspace_id, "workspace_id")
        if self.owner_user_id is not None:
            _require_id(self.owner_user_id, "owner_user_id")


@dataclass(frozen=True, slots=True)
class NoEnvironment:
    """Select no tool runtime for the Thread or Run."""

    mode: str = field(default="none", init=False)


@dataclass(frozen=True, slots=True)
class AutomaticEnvironment:
    """Lazily resolve or provision an environment using a profile."""

    profile_id: str
    mode: str = field(default="automatic", init=False)

    def __post_init__(self) -> None:
        _require_id(self.profile_id, "profile_id")


@dataclass(frozen=True, slots=True)
class ExistingEnvironment:
    """Select one existing logical environment explicitly."""

    environment_id: str
    mode: str = field(default="existing", init=False)

    def __post_init__(self) -> None:
        _require_id(self.environment_id, "environment_id")


EnvironmentSelection: TypeAlias = (
    NoEnvironment | AutomaticEnvironment | ExistingEnvironment
)


@dataclass(frozen=True, slots=True)
class ThreadRuntimeBinding:
    """Atomic durable-workspace and live-environment selection for a Thread.

    A coding environment must operate on the Thread's Workspace, so automatic
    and explicit environment selections are invalid without ``workspace_id``.
    The environment registry performs the remaining cross-resource check that
    an explicit environment's ``workspace_id`` matches this binding.
    """

    workspace_id: str | None = None
    environment_selection: EnvironmentSelection = field(default_factory=NoEnvironment)

    def __post_init__(self) -> None:
        if self.workspace_id is not None:
            _require_id(self.workspace_id, "workspace_id")
        if not isinstance(
            self.environment_selection,
            (NoEnvironment, AutomaticEnvironment, ExistingEnvironment),
        ):
            raise TypeError("environment_selection has an unsupported type")
        if (
            not isinstance(self.environment_selection, NoEnvironment)
            and self.workspace_id is None
        ):
            raise ValueError("an environment selection requires workspace_id")

    @property
    def environment_id(self) -> str | None:
        """Return the explicit environment id, if this is an existing binding."""

        selection = self.environment_selection
        if isinstance(selection, ExistingEnvironment):
            return selection.environment_id
        return None

    def validate_environment(self, environment: ExecutionEnvironmentDescriptor) -> None:
        """Reject an explicit environment that belongs to another Workspace."""

        selected_id = self.environment_id
        if selected_id is None:
            raise ValueError("binding does not select an existing environment")
        if environment.environment_id != selected_id:
            raise ValueError(
                "resolved environment does not match the selected environment_id"
            )
        if environment.workspace_id != self.workspace_id:
            raise ValueError("selected environment belongs to a different workspace")


@dataclass(frozen=True, slots=True)
class ExecutionEnvironmentDescriptor:
    """Serializable identity, lifecycle, and capability state for a runtime."""

    environment_id: str
    workspace_id: str
    kind: EnvironmentKind
    state: EnvironmentState
    generation: int
    capabilities: frozenset[EnvironmentCapability] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        _require_id(self.environment_id, "environment_id")
        _require_id(self.workspace_id, "workspace_id")
        if not isinstance(self.kind, EnvironmentKind):
            raise TypeError("kind must be an EnvironmentKind")
        if not isinstance(self.state, EnvironmentState):
            raise TypeError("state must be an EnvironmentState")
        if not isinstance(self.generation, int) or isinstance(self.generation, bool):
            raise TypeError("generation must be an integer")
        if self.generation < 1:
            raise ValueError("generation must be at least 1")
        if not isinstance(self.capabilities, frozenset) or not all(
            isinstance(capability, EnvironmentCapability)
            for capability in self.capabilities
        ):
            raise TypeError(
                "capabilities must be a frozenset of EnvironmentCapability values"
            )


@dataclass(frozen=True, slots=True)
class SandboxExecutionRuntime:
    """Environment-owned SDK sandbox adapter state.

    The mutable SDK session is deliberately carried by an environment lease,
    never by an :class:`AgentSpec`.  A Run that captures the lease therefore
    keeps using one environment generation even if the logical environment is
    replaced while that Run is active.
    """

    session: Any
    run_as: str | None = None

    def __post_init__(self) -> None:
        if self.session is None:
            raise ValueError("sandbox runtime requires a session")
        if self.run_as is not None:
            _require_id(self.run_as, "run_as")


@dataclass(frozen=True, slots=True)
class TerminalExecutionDefaults:
    """Environment-owned defaults for interactive terminal creation."""

    shell: str | None = None
    cwd: str | None = None
    user: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("shell", "cwd", "user"):
            value = getattr(self, field_name)
            if value is not None:
                _require_id(value, field_name)


@dataclass(frozen=True, slots=True)
class EnvironmentLease:
    """Immutable operation-scoped access to one environment generation."""

    descriptor: ExecutionEnvironmentDescriptor
    workspace: Workspace
    lifecycle_controller: Any | None = None
    sandbox_runtime: SandboxExecutionRuntime | None = None
    terminal_defaults: TerminalExecutionDefaults = field(
        default_factory=TerminalExecutionDefaults
    )

    def __post_init__(self) -> None:
        if self.descriptor.state is not EnvironmentState.READY:
            raise ValueError("an environment lease requires a ready environment")

    @property
    def environment_id(self) -> str:
        return self.descriptor.environment_id

    @property
    def workspace_id(self) -> str:
        return self.descriptor.workspace_id

    @property
    def generation(self) -> int:
        return self.descriptor.generation

    def require(self, *capabilities: EnvironmentCapability) -> None:
        """Raise when the environment lacks any requested capability."""

        missing = set(capabilities) - self.descriptor.capabilities
        if missing:
            names = ", ".join(sorted(capability.value for capability in missing))
            raise ValueError(f"environment lacks required capabilities: {names}")
