"""Central execution-resource resolution for RPC and extension operations."""

from __future__ import annotations

from dataclasses import dataclass, replace
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

_OPTIONAL_CAPABILITY_BY_REQUIREMENT = {
    ExecutionRequirement.OPTIONAL_WORKSPACE_READ: EnvironmentCapability.WORKSPACE_READ,
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
    automatic_environment: AutomaticEnvironment | None = None

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
        run_plan = _RunPlanResolution()

        if requirements & ExecutionRequirement.RUN_PLAN:
            run_plan = await self._resolve_run_plan(operation.name, params)
            environment_id = run_plan.environment_id
            expected_generation = run_plan.expected_generation
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
        elif requirements & ExecutionRequirement.OPTIONAL_WORKSPACE_READ:
            # Some extension operations are control-plane valid without an
            # environment but may consume a workspace-local override when the
            # caller explicitly supplies one. Resolve that optional binding in
            # the same router as every mandatory lease; handlers never inspect
            # session paths or reconstruct an execution backend themselves.
            if environment_id is not None:
                expected_generation = params.get("environment_generation")
                if expected_generation is not None and (
                    not isinstance(expected_generation, int)
                    or isinstance(expected_generation, bool)
                ):
                    raise TypeError("environment_generation must be an integer")
                required_capabilities = frozenset(
                    capability
                    for requirement, capability in _OPTIONAL_CAPABILITY_BY_REQUIREMENT.items()
                    if requirements & requirement
                )
                lease = await self._environment_manager.acquire(
                    environment_id,
                    expected_generation=expected_generation,
                    required_capabilities=required_capabilities,
                )
                environment = lease.descriptor
        resolved_workspace_id = (
            environment.workspace_id
            if environment is not None
            else run_plan.workspace_id
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
            automatic_environment=run_plan.automatic_environment,
        )

    async def materialize(self, context: RequestContext) -> RequestContext:
        """Provision a resolved automatic Run target after authorization.

        ``prepare`` is intentionally side-effect free because RPC dispatch uses
        it before authorization. The authorized ``start_run`` handler calls
        this method to turn an automatic profile/Workspace selection into one
        immutable environment lease.
        """

        selection = context.automatic_environment
        if selection is None:
            return context
        workspace = context.workspace
        if workspace is None:
            raise RuntimeError("automatic environment has no resolved workspace")
        descriptor = await self._environment_manager.resolve_automatic(
            workspace.workspace_id, selection.profile_id
        )
        if descriptor.workspace_id != workspace.workspace_id:
            raise WorkspaceEnvironmentMismatchError(
                workspace.workspace_id,
                descriptor.environment_id,
                descriptor.workspace_id,
            )
        lease = await self._environment_manager.acquire(
            descriptor.environment_id,
            expected_generation=descriptor.generation,
        )
        return replace(
            context,
            environment=descriptor,
            environment_lease=lease,
            automatic_environment=None,
        )

    async def _resolve_run_plan(
        self, operation_name: str, params: Mapping[str, Any]
    ) -> _RunPlanResolution:
        selection = params.get("environment_selection")
        if not isinstance(selection, Mapping):
            raise TypeError("environment_selection must be an object")
        mode = selection.get("mode")
        if mode == "none":
            return _RunPlanResolution()
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
            return _RunPlanResolution(environment_id, generation)
        if mode == "inherit":
            thread_id = self._identifier(params, "thread_id") or self._identifier(
                params, "session_id"
            )
            if thread_id is None:
                raise TypeError("inherit environment selection requires thread_id")
            binding = await self._thread_binding_registry.ensure(thread_id)
            selected = binding.environment_selection
            if isinstance(selected, NoEnvironment):
                return _RunPlanResolution(workspace_id=binding.workspace_id)
            if isinstance(selected, ExistingEnvironment):
                return _RunPlanResolution(
                    selected.environment_id,
                    workspace_id=binding.workspace_id,
                )
            if isinstance(selected, AutomaticEnvironment):
                return _RunPlanResolution(
                    workspace_id=binding.workspace_id,
                    automatic_environment=selected,
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


@dataclass(frozen=True, slots=True)
class _RunPlanResolution:
    environment_id: str | None = None
    expected_generation: int | None = None
    workspace_id: str | None = None
    automatic_environment: AutomaticEnvironment | None = None
