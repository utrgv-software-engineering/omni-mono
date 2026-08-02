"""Central execution-resource resolution for RPC and extension operations."""

from __future__ import annotations

from dataclasses import dataclass
from contextvars import ContextVar, Token
from typing import Any, Mapping

from omniagents.rpc.protocol_catalog import (
    ExecutionRequirement,
    ProtocolOperation,
    ResourceScope,
)

from .errors import (
    EnvironmentRequiredError,
    WorkspaceEnvironmentMismatchError,
)
from .models import (
    AutomaticEnvironment,
    EnvironmentCapability,
    EnvironmentLease,
    ExistingEnvironment,
    ExecutionEnvironmentDescriptor,
    NoEnvironment,
    WorkspaceResource,
)
from .registry import (
    ExecutionEnvironmentManager,
    ThreadRuntimeBindingRegistry,
    WorkspaceRegistry,
)

_CURRENT_REQUEST_CONTEXT: ContextVar[RequestContext | None] = ContextVar(
    "omniagents_request_context", default=None
)

_LEASE_REQUIREMENTS = (
    ExecutionRequirement.WORKSPACE_READ
    | ExecutionRequirement.WORKSPACE_WRITE
    | ExecutionRequirement.PROCESS
    | ExecutionRequirement.NETWORK
    | ExecutionRequirement.PTY
    | ExecutionRequirement.ENVIRONMENT_LIFECYCLE
)

_CAPABILITY_BY_REQUIREMENT = {
    ExecutionRequirement.WORKSPACE_READ: EnvironmentCapability.WORKSPACE_READ,
    ExecutionRequirement.WORKSPACE_WRITE: EnvironmentCapability.WORKSPACE_WRITE,
    ExecutionRequirement.PROCESS: EnvironmentCapability.PROCESS,
    ExecutionRequirement.NETWORK: EnvironmentCapability.NETWORK,
    ExecutionRequirement.PTY: EnvironmentCapability.PTY,
    ExecutionRequirement.ENVIRONMENT_LIFECYCLE: (
        EnvironmentCapability.ENVIRONMENT_LIFECYCLE
    ),
}


@dataclass(frozen=True, slots=True)
class OperationExecutionDescriptor:
    """Common descriptor accepted from typed RPCs and server functions."""

    name: str
    resource_scope: ResourceScope
    execution_requirements: ExecutionRequirement

    @classmethod
    def from_protocol(
        cls, operation: ProtocolOperation
    ) -> OperationExecutionDescriptor:
        return cls(
            name=operation.name,
            resource_scope=operation.resource_scope,
            execution_requirements=operation.execution_requirements,
        )


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Resolved resource identities injected into an operation handler."""

    operation: OperationExecutionDescriptor
    principal: Any | None
    agent_host_id: str | None = None
    agent_definition_id: str | None = None
    thread_id: str | None = None
    run_id: str | None = None
    workspace: WorkspaceResource | None = None
    environment: ExecutionEnvironmentDescriptor | None = None
    environment_lease: EnvironmentLease | None = None

    @property
    def resource_owner_user_id(self) -> str | None:
        """Owner used by the central authorization decision, if resolved."""

        return self.workspace.owner_user_id if self.workspace is not None else None


def current_request_context() -> RequestContext | None:
    """Return the task-local resolved request context, if dispatch is active."""

    return _CURRENT_REQUEST_CONTEXT.get()


def require_request_context() -> RequestContext:
    """Return the operation-scoped context or reject direct, unrouted use.

    Execution-sensitive handlers use this instead of consulting process-global
    serve state or selecting a host backend when dispatch forgot to bind an
    environment.
    """

    context = current_request_context()
    if context is None:
        raise RuntimeError("operation requires a routed request context")
    return context


def require_environment_lease() -> EnvironmentLease:
    """Return the immutable lease bound by :class:`OperationRouter`."""

    context = require_request_context()
    if context.environment_lease is None:
        raise RuntimeError(
            f"operation {context.operation.name!r} requires an environment lease"
        )
    return context.environment_lease


def bind_request_context(context: RequestContext) -> Token:
    """Bind a resolved context for authorization and handler execution."""

    return _CURRENT_REQUEST_CONTEXT.set(context)


def reset_request_context(token: Token) -> None:
    """Restore the prior task-local context after dispatch."""

    _CURRENT_REQUEST_CONTEXT.reset(token)


class OperationRouter:
    """Resolve an operation without any implicit execution substrate.

    The router only understands resource IDs and registered managers. It never
    reads session variables, process-global sandbox state, a filesystem path,
    or the server's current working directory.
    """

    def __init__(
        self,
        workspace_registry: WorkspaceRegistry,
        environment_manager: ExecutionEnvironmentManager,
        thread_binding_registry: ThreadRuntimeBindingRegistry,
        *,
        agent_host_id: str | None = None,
    ) -> None:
        self._workspace_registry = workspace_registry
        self._environment_manager = environment_manager
        self._thread_binding_registry = thread_binding_registry
        self._agent_host_id = agent_host_id

    async def prepare_protocol(
        self,
        operation: ProtocolOperation,
        params: Mapping[str, Any],
        *,
        principal: Any | None = None,
    ) -> RequestContext:
        return await self.prepare(
            OperationExecutionDescriptor.from_protocol(operation),
            params,
            principal=principal,
        )

    async def prepare(
        self,
        operation: OperationExecutionDescriptor,
        params: Mapping[str, Any],
        *,
        principal: Any | None = None,
    ) -> RequestContext:
        if not isinstance(operation.resource_scope, ResourceScope):
            raise TypeError("operation resource_scope must be a ResourceScope")
        requirements = operation.execution_requirements
        if not isinstance(requirements, ExecutionRequirement):
            raise TypeError(
                "operation execution_requirements must be an ExecutionRequirement"
            )
        if requirements & ExecutionRequirement.EXTENSION_DECLARED:
            raise TypeError(
                f"operation {operation.name!r} requires its extension descriptor"
            )

        environment_id = self._identifier(params, "environment_id")
        requested_workspace_id = self._identifier(params, "workspace_id")
        environment: ExecutionEnvironmentDescriptor | None = None
        lease: EnvironmentLease | None = None

        if requirements & ExecutionRequirement.RUN_PLAN:
            environment_id, expected_generation = await self._resolve_run_plan(
                operation.name, params
            )
            if environment_id is not None:
                lease = await self._environment_manager.acquire(
                    environment_id,
                    expected_generation=expected_generation,
                )
                environment = lease.descriptor
        elif requirements & _LEASE_REQUIREMENTS:
            if environment_id is None:
                raise EnvironmentRequiredError(operation.name)
            required_capabilities = frozenset(
                capability
                for requirement, capability in _CAPABILITY_BY_REQUIREMENT.items()
                if requirements & requirement
            )
            expected_generation = params.get("environment_generation")
            if expected_generation is not None and (
                not isinstance(expected_generation, int)
                or isinstance(expected_generation, bool)
            ):
                raise TypeError("environment_generation must be an integer")
            lease = await self._environment_manager.acquire(
                environment_id,
                expected_generation=expected_generation,
                required_capabilities=required_capabilities,
            )
            environment = lease.descriptor
        resolved_workspace_id = (
            environment.workspace_id if environment is not None else None
        )
        if (
            requested_workspace_id is not None
            and resolved_workspace_id is not None
            and requested_workspace_id != resolved_workspace_id
        ):
            raise WorkspaceEnvironmentMismatchError(
                requested_workspace_id,
                environment_id or "",
                resolved_workspace_id,
            )
        workspace_id = requested_workspace_id or resolved_workspace_id
        workspace = (
            self._workspace_registry.get(workspace_id)
            if workspace_id is not None
            else None
        )

        return RequestContext(
            operation=operation,
            principal=principal,
            agent_host_id=self._agent_host_id,
            agent_definition_id=self._identifier(params, "agent_definition_id"),
            thread_id=(
                self._identifier(params, "thread_id")
                or self._identifier(params, "session_id")
            ),
            run_id=self._identifier(params, "run_id"),
            workspace=workspace,
            environment=environment,
            environment_lease=lease,
        )

    async def _resolve_run_plan(
        self, operation_name: str, params: Mapping[str, Any]
    ) -> tuple[str | None, int | None]:
        selection = params.get("environment_selection")
        if not isinstance(selection, Mapping):
            raise TypeError("environment_selection must be an object")
        mode = selection.get("mode")
        if mode == "none":
            return None, None
        if mode == "explicit":
            environment_id = self._identifier(selection, "environment_id")
            if environment_id is None:
                raise EnvironmentRequiredError(operation_name)
            generation = selection.get("environment_generation")
            if generation is not None and (
                not isinstance(generation, int)
                or isinstance(generation, bool)
                or generation < 1
            ):
                raise TypeError("environment_generation must be an integer >= 1")
            return environment_id, generation
        if mode == "inherit":
            thread_id = self._identifier(params, "thread_id") or self._identifier(
                params, "session_id"
            )
            if thread_id is None:
                raise TypeError("inherit environment selection requires thread_id")
            binding = await self._thread_binding_registry.ensure(thread_id)
            selected = binding.environment_selection
            if isinstance(selected, NoEnvironment):
                return None, None
            if isinstance(selected, ExistingEnvironment):
                return selected.environment_id, None
            if isinstance(selected, AutomaticEnvironment):
                raise RuntimeError(
                    "automatic environment provisioning is not implemented"
                )
        raise TypeError(f"unsupported environment selection mode: {mode!r}")

    @staticmethod
    def _identifier(params: Mapping[str, Any], name: str) -> str | None:
        value = params.get(name)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise TypeError(f"{name} must be a non-empty string")
        return value
