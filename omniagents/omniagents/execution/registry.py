"""Explicit registries for durable workspaces and live tool environments."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from functools import partial
import uuid

from omniagents.workspace.base import Workspace

from .errors import (
    CapabilityUnavailableError,
    EnvironmentChangedError,
    EnvironmentNotFoundError,
    EnvironmentProvisioningFailedError,
    EnvironmentProvisioningUnavailableError,
    EnvironmentUnavailableError,
    WorkspaceEnvironmentMismatchError,
    WorkspaceNotFoundError,
)
from .provisioning import (
    EnvironmentProvisioner,
    EnvironmentProvisionRequest,
    ProvisionedEnvironment,
)
from .models import (
    EnvironmentCapability,
    EnvironmentLease,
    EnvironmentState,
    ExistingEnvironment,
    ExecutionEnvironmentDescriptor,
    SandboxExecutionRuntime,
    TerminalExecutionDefaults,
    ThreadRuntimeBinding,
    WorkspaceResource,
)


class WorkspaceRegistry:
    """Registry of durable Workspace identities; it has no implicit default."""

    def __init__(self) -> None:
        self._workspaces: dict[str, WorkspaceResource] = {}

    def register(self, workspace: WorkspaceResource) -> None:
        if workspace.workspace_id in self._workspaces:
            raise ValueError(
                f"workspace {workspace.workspace_id!r} is already registered"
            )
        self._workspaces[workspace.workspace_id] = workspace

    def get(self, workspace_id: str) -> WorkspaceResource:
        try:
            return self._workspaces[workspace_id]
        except KeyError:
            raise WorkspaceNotFoundError(workspace_id) from None

    def list(self) -> tuple[WorkspaceResource, ...]:
        return tuple(self._workspaces.values())


class ThreadRuntimeBindingRegistry:
    """Atomically own each Thread's workspace/environment selection.

    Bindings are validated against the same workspace and environment
    registries used by operation routing. A failed rebind leaves the prior
    binding untouched; callers never observe a partially updated pair of
    independent workspace and environment fields.
    """

    def __init__(
        self,
        workspace_registry: WorkspaceRegistry,
        environment_manager: "ExecutionEnvironmentManager",
    ) -> None:
        self._workspace_registry = workspace_registry
        self._environment_manager = environment_manager
        self._bindings: dict[str, ThreadRuntimeBinding] = {}
        self._lock = asyncio.Lock()

    async def register(
        self, thread_id: str, binding: ThreadRuntimeBinding
    ) -> ThreadRuntimeBinding:
        self._require_thread_id(thread_id)
        await self._validate(binding)
        async with self._lock:
            if thread_id in self._bindings:
                raise ValueError(f"thread {thread_id!r} already has a runtime binding")
            self._bindings[thread_id] = binding
            return binding

    async def rebind(
        self, thread_id: str, binding: ThreadRuntimeBinding
    ) -> ThreadRuntimeBinding:
        self._require_thread_id(thread_id)
        await self._validate(binding)
        async with self._lock:
            if thread_id not in self._bindings:
                raise KeyError(f"thread {thread_id!r} has no runtime binding")
            self._bindings[thread_id] = binding
            return binding

    async def bind(
        self, thread_id: str, binding: ThreadRuntimeBinding
    ) -> ThreadRuntimeBinding:
        """Atomically create or replace one Thread's complete binding."""

        self._require_thread_id(thread_id)
        await self._validate(binding)
        async with self._lock:
            self._bindings[thread_id] = binding
            return binding

    async def get(self, thread_id: str) -> ThreadRuntimeBinding:
        self._require_thread_id(thread_id)
        async with self._lock:
            try:
                return self._bindings[thread_id]
            except KeyError:
                raise KeyError(f"thread {thread_id!r} has no runtime binding") from None

    async def ensure(self, thread_id: str) -> ThreadRuntimeBinding:
        """Return the binding, atomically creating a targetless one if absent."""

        self._require_thread_id(thread_id)
        async with self._lock:
            return self._bindings.setdefault(thread_id, ThreadRuntimeBinding())

    async def remove(self, thread_id: str) -> ThreadRuntimeBinding | None:
        self._require_thread_id(thread_id)
        async with self._lock:
            return self._bindings.pop(thread_id, None)

    async def _validate(self, binding: ThreadRuntimeBinding) -> None:
        if not isinstance(binding, ThreadRuntimeBinding):
            raise TypeError("binding must be a ThreadRuntimeBinding")
        if binding.workspace_id is not None:
            self._workspace_registry.get(binding.workspace_id)
        selection = binding.environment_selection
        if isinstance(selection, ExistingEnvironment):
            environment = await self._environment_manager.describe(
                selection.environment_id
            )
            if environment.workspace_id != binding.workspace_id:
                raise WorkspaceEnvironmentMismatchError(
                    binding.workspace_id or "",
                    environment.environment_id,
                    environment.workspace_id,
                )

    @staticmethod
    def _require_thread_id(thread_id: str) -> None:
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id must be a non-empty string")


@dataclass(frozen=True, slots=True)
class _EnvironmentRecord:
    descriptor: ExecutionEnvironmentDescriptor
    workspace: Workspace
    lifecycle_controller: object | None = None
    sandbox_runtime: SandboxExecutionRuntime | None = None
    terminal_defaults: TerminalExecutionDefaults = TerminalExecutionDefaults()


_PRESERVE_RUNTIME = object()


class ExecutionEnvironmentManager:
    """Own environment identity, state, generations, and lease acquisition.

    The manager deliberately has no "current" environment and never constructs
    a host workspace. Every lookup names an environment ID, and every registered
    environment names an already-registered durable Workspace.
    """

    def __init__(
        self,
        workspace_registry: WorkspaceRegistry,
        provisioner: EnvironmentProvisioner | None = None,
    ) -> None:
        self._workspace_registry = workspace_registry
        self._provisioner = provisioner
        self._environments: dict[str, _EnvironmentRecord] = {}
        self._automatic_environment_ids: dict[tuple[str, str], str] = {}
        self._provisioning_tasks: dict[
            tuple[str, str], asyncio.Task[ExecutionEnvironmentDescriptor]
        ] = {}
        self._lock = asyncio.Lock()

    def set_provisioner(self, provisioner: EnvironmentProvisioner) -> None:
        """Install the AgentHost's product provisioner during composition."""

        if not callable(getattr(provisioner, "provision", None)):
            raise TypeError("provisioner must implement provision(request)")
        if self._provisioner is not None and self._provisioner is not provisioner:
            raise RuntimeError("environment provisioner is already configured")
        self._provisioner = provisioner

    async def resolve_automatic(
        self, workspace_id: str, profile_id: str
    ) -> ExecutionEnvironmentDescriptor:
        """Resolve or lazily provision one profile/Workspace environment.

        Concurrent callers share one provisioning task. A stopped or failed
        runtime is replaced by a new logical environment; ready runtimes are
        reused across Threads that intentionally select the same pair.
        """

        workspace = self._workspace_registry.get(workspace_id)
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise ValueError("profile_id must be a non-empty string")
        key = (workspace_id, profile_id)
        async with self._lock:
            environment_id = self._automatic_environment_ids.get(key)
            if environment_id is not None:
                descriptor = self._record(environment_id).descriptor
                if descriptor.state not in {
                    EnvironmentState.STOPPED,
                    EnvironmentState.FAILED,
                }:
                    return descriptor
                self._automatic_environment_ids.pop(key, None)

            task = self._provisioning_tasks.get(key)
            if task is None:
                if self._provisioner is None:
                    raise EnvironmentProvisioningUnavailableError(
                        workspace_id, profile_id
                    )
                request = EnvironmentProvisionRequest(
                    environment_id=f"environment_{uuid.uuid4().hex}",
                    workspace=workspace,
                    profile_id=profile_id,
                )
                task = asyncio.create_task(
                    self._provision_automatic(key, request),
                    name=f"provision_{request.environment_id}",
                )
                self._provisioning_tasks[key] = task
                task.add_done_callback(partial(self._provisioning_done, key))

        return await asyncio.shield(task)

    async def _provision_automatic(
        self,
        key: tuple[str, str],
        request: EnvironmentProvisionRequest,
    ) -> ExecutionEnvironmentDescriptor:
        assert self._provisioner is not None
        try:
            provisioned = await self._provisioner.provision(request)
            if not isinstance(provisioned, ProvisionedEnvironment):
                raise TypeError("provisioner must return ProvisionedEnvironment")
        except Exception as exc:
            if isinstance(exc, EnvironmentProvisioningFailedError):
                raise
            raise EnvironmentProvisioningFailedError(
                request.workspace.workspace_id,
                request.profile_id,
                str(exc),
            ) from exc

        descriptor = ExecutionEnvironmentDescriptor(
            environment_id=request.environment_id,
            workspace_id=request.workspace.workspace_id,
            kind=provisioned.kind,
            state=EnvironmentState.READY,
            generation=1,
            capabilities=provisioned.capabilities,
        )
        async with self._lock:
            self._register_initial(
                descriptor,
                provisioned.workspace,
                lifecycle_controller=provisioned.lifecycle_controller,
                sandbox_runtime=provisioned.sandbox_runtime,
                terminal_defaults=provisioned.terminal_defaults,
            )
            self._automatic_environment_ids[key] = descriptor.environment_id
        return descriptor

    def _provisioning_done(
        self,
        key: tuple[str, str],
        task: asyncio.Task[ExecutionEnvironmentDescriptor],
    ) -> None:
        """Consume detached failures and remove completed single-flight tasks."""

        if not task.cancelled():
            task.exception()
        asyncio.create_task(self._remove_provisioning_task(key, task))

    async def _remove_provisioning_task(
        self,
        key: tuple[str, str],
        task: asyncio.Task[ExecutionEnvironmentDescriptor],
    ) -> None:
        async with self._lock:
            if self._provisioning_tasks.get(key) is task:
                self._provisioning_tasks.pop(key, None)

    async def register(
        self,
        descriptor: ExecutionEnvironmentDescriptor,
        workspace: Workspace,
        *,
        lifecycle_controller: object | None = None,
        sandbox_runtime: SandboxExecutionRuntime | None = None,
        terminal_defaults: TerminalExecutionDefaults = TerminalExecutionDefaults(),
    ) -> None:
        self._workspace_registry.get(descriptor.workspace_id)
        async with self._lock:
            self._register_initial(
                descriptor,
                workspace,
                lifecycle_controller=lifecycle_controller,
                sandbox_runtime=sandbox_runtime,
                terminal_defaults=terminal_defaults,
            )

    def register_initial(
        self,
        descriptor: ExecutionEnvironmentDescriptor,
        workspace: Workspace,
        *,
        lifecycle_controller: object | None = None,
        sandbox_runtime: SandboxExecutionRuntime | None = None,
        terminal_defaults: TerminalExecutionDefaults = TerminalExecutionDefaults(),
    ) -> None:
        """Register during single-threaded AgentHost composition.

        Dynamic registration after the host starts must use :meth:`register`.
        """

        self._workspace_registry.get(descriptor.workspace_id)
        self._register_initial(
            descriptor,
            workspace,
            lifecycle_controller=lifecycle_controller,
            sandbox_runtime=sandbox_runtime,
            terminal_defaults=terminal_defaults,
        )

    def _register_initial(
        self,
        descriptor: ExecutionEnvironmentDescriptor,
        workspace: Workspace,
        *,
        lifecycle_controller: object | None = None,
        sandbox_runtime: SandboxExecutionRuntime | None = None,
        terminal_defaults: TerminalExecutionDefaults = TerminalExecutionDefaults(),
    ) -> None:
        if sandbox_runtime is not None and not isinstance(
            sandbox_runtime, SandboxExecutionRuntime
        ):
            raise TypeError("sandbox_runtime must be a SandboxExecutionRuntime")
        if not isinstance(terminal_defaults, TerminalExecutionDefaults):
            raise TypeError("terminal_defaults must be TerminalExecutionDefaults")
        if descriptor.environment_id in self._environments:
            raise ValueError(
                f"environment {descriptor.environment_id!r} is already registered"
            )
        self._environments[descriptor.environment_id] = _EnvironmentRecord(
            descriptor,
            workspace,
            lifecycle_controller,
            sandbox_runtime,
            terminal_defaults,
        )

    async def describe(self, environment_id: str) -> ExecutionEnvironmentDescriptor:
        async with self._lock:
            return self._record(environment_id).descriptor

    async def list(self) -> tuple[ExecutionEnvironmentDescriptor, ...]:
        async with self._lock:
            return tuple(record.descriptor for record in self._environments.values())

    async def sandbox_runtime(
        self, environment_id: str
    ) -> SandboxExecutionRuntime | None:
        """Return lifecycle-owned SDK state for control-plane cleanup.

        Run and RPC routing must use :meth:`acquire`; this state-independent
        accessor exists for shutdown paths that must close a failed or stopped
        environment whose ready lease can no longer be acquired.
        """

        async with self._lock:
            return self._record(environment_id).sandbox_runtime

    async def acquire(
        self,
        environment_id: str,
        *,
        expected_generation: int | None = None,
        required_capabilities: frozenset[EnvironmentCapability] = frozenset(),
    ) -> EnvironmentLease:
        async with self._lock:
            record = self._record(environment_id)
            descriptor = record.descriptor
            if descriptor.state is not EnvironmentState.READY:
                raise EnvironmentUnavailableError(
                    environment_id, descriptor.state.value
                )
            if (
                expected_generation is not None
                and descriptor.generation != expected_generation
            ):
                raise EnvironmentChangedError(
                    environment_id, expected_generation, descriptor.generation
                )
            missing = required_capabilities - descriptor.capabilities
            if missing:
                raise CapabilityUnavailableError(
                    environment_id,
                    frozenset(capability.value for capability in missing),
                )
            return EnvironmentLease(
                descriptor,
                record.workspace,
                record.lifecycle_controller,
                record.sandbox_runtime,
                record.terminal_defaults,
            )

    async def set_lifecycle_controller(
        self, environment_id: str, controller: object | None
    ) -> None:
        """Attach environment-scoped control-plane behavior.

        The controller is deliberately carried by leases rather than stored on
        a process-global serve singleton. Replacing the environment preserves
        it because it represents the logical environment, not one backend
        generation.
        """

        async with self._lock:
            current = self._record(environment_id)
            self._environments[environment_id] = _EnvironmentRecord(
                current.descriptor,
                current.workspace,
                controller,
                current.sandbox_runtime,
                current.terminal_defaults,
            )

    async def replace(
        self,
        environment_id: str,
        workspace: Workspace,
        *,
        capabilities: frozenset[EnvironmentCapability] | None = None,
        sandbox_runtime: SandboxExecutionRuntime | None | object = _PRESERVE_RUNTIME,
        terminal_defaults: TerminalExecutionDefaults | object = _PRESERVE_RUNTIME,
    ) -> ExecutionEnvironmentDescriptor:
        """Install a new ready generation without mutating existing leases."""

        if sandbox_runtime is not _PRESERVE_RUNTIME and (
            sandbox_runtime is not None
            and not isinstance(sandbox_runtime, SandboxExecutionRuntime)
        ):
            raise TypeError("sandbox_runtime must be a SandboxExecutionRuntime")
        if terminal_defaults is not _PRESERVE_RUNTIME and not isinstance(
            terminal_defaults, TerminalExecutionDefaults
        ):
            raise TypeError("terminal_defaults must be TerminalExecutionDefaults")

        async with self._lock:
            current = self._record(environment_id)
            descriptor = current.descriptor
            self._workspace_registry.get(descriptor.workspace_id)
            next_descriptor = replace(
                descriptor,
                state=EnvironmentState.READY,
                generation=descriptor.generation + 1,
                capabilities=(
                    descriptor.capabilities if capabilities is None else capabilities
                ),
            )
            resolved_runtime = (
                current.sandbox_runtime
                if sandbox_runtime is _PRESERVE_RUNTIME
                else sandbox_runtime
            )
            resolved_terminal_defaults = (
                current.terminal_defaults
                if terminal_defaults is _PRESERVE_RUNTIME
                else terminal_defaults
            )
            assert resolved_runtime is None or isinstance(
                resolved_runtime, SandboxExecutionRuntime
            )
            assert isinstance(resolved_terminal_defaults, TerminalExecutionDefaults)
            self._environments[environment_id] = _EnvironmentRecord(
                next_descriptor,
                workspace,
                current.lifecycle_controller,
                resolved_runtime,
                resolved_terminal_defaults,
            )
            return next_descriptor

    async def transition(
        self, environment_id: str, state: EnvironmentState
    ) -> ExecutionEnvironmentDescriptor:
        """Publish a non-ready lifecycle state without changing generation."""

        if state is EnvironmentState.READY:
            raise ValueError("use register or replace to publish a ready environment")
        async with self._lock:
            current = self._record(environment_id)
            transitioned = replace(current.descriptor, state=state)
            self._environments[environment_id] = _EnvironmentRecord(
                transitioned,
                current.workspace,
                current.lifecycle_controller,
                current.sandbox_runtime,
                current.terminal_defaults,
            )
            return transitioned

    async def stop(self, environment_id: str) -> ExecutionEnvironmentDescriptor:
        closing_lease: EnvironmentLease
        async with self._lock:
            current = self._record(environment_id)
            descriptor = current.descriptor
            if descriptor.state is not EnvironmentState.READY:
                raise EnvironmentUnavailableError(
                    environment_id, descriptor.state.value
                )
            closing_lease = EnvironmentLease(
                descriptor,
                current.workspace,
                current.lifecycle_controller,
                current.sandbox_runtime,
                current.terminal_defaults,
            )
            stopping = replace(descriptor, state=EnvironmentState.STOPPING)
            self._environments[environment_id] = _EnvironmentRecord(
                stopping,
                current.workspace,
                current.lifecycle_controller,
                current.sandbox_runtime,
                current.terminal_defaults,
            )

        close = getattr(closing_lease.lifecycle_controller, "close", None)
        if callable(close):
            try:
                await close(closing_lease)
            except Exception:
                async with self._lock:
                    failed_current = self._record(environment_id)
                    failed = replace(
                        failed_current.descriptor, state=EnvironmentState.FAILED
                    )
                    self._environments[environment_id] = replace(
                        failed_current, descriptor=failed
                    )
                raise

        async with self._lock:
            current = self._record(environment_id)
            stopped = replace(current.descriptor, state=EnvironmentState.STOPPED)
            self._environments[environment_id] = _EnvironmentRecord(
                stopped,
                current.workspace,
                None,
                None,
                current.terminal_defaults,
            )
            return stopped

    async def validate_workspace(self, workspace_id: str, environment_id: str) -> None:
        self._workspace_registry.get(workspace_id)
        async with self._lock:
            descriptor = self._record(environment_id).descriptor
            if descriptor.workspace_id != workspace_id:
                raise WorkspaceEnvironmentMismatchError(
                    workspace_id, environment_id, descriptor.workspace_id
                )

    def _record(self, environment_id: str) -> _EnvironmentRecord:
        try:
            return self._environments[environment_id]
        except KeyError:
            raise EnvironmentNotFoundError(environment_id) from None
