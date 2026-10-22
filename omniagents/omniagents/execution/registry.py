"""Explicit registries for durable workspaces and live tool environments."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
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
    EnvironmentShutdownError,
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
            expected_generation = selection.environment_generation
            if (
                expected_generation is not None
                and environment.generation != expected_generation
            ):
                raise EnvironmentChangedError(
                    environment.environment_id,
                    expected_generation,
                    environment.generation,
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
        self._shutting_down = False
        self._lock = asyncio.Lock()
        self._run_tasks: dict[
            str, dict[asyncio.Task, Callable[[], Awaitable[object]]]
        ] = {}
        self._resource_tasks: dict[
            str, dict[asyncio.Task, Callable[[], Awaitable[object]]]
        ] = {}
        self._stop_tasks: dict[str, asyncio.Task[ExecutionEnvironmentDescriptor]] = {}

    async def start_run_task(
        self,
        lease: EnvironmentLease,
        start: Callable[[], asyncio.Task],
        cancel: Callable[[], Awaitable[object]],
    ) -> asyncio.Task:
        """Register a run atomically with the environment's shutdown fence.

        The caller may have acquired its lease before an intervening await.
        Revalidate it here, before constructing or publishing the task. The
        cancellation callback must address this exact run, not its session.
        """
        return await self._start_owned_task(lease, start, cancel, self._run_tasks)

    async def start_resource_task(
        self,
        lease: EnvironmentLease,
        start: Callable[[], asyncio.Task],
        close: Callable[[], Awaitable[object]],
    ) -> asyncio.Task:
        """Own an auxiliary worker until retirement, after runs finish.

        Resource creation shares the run admission fence. A resource belongs
        to an exact lease, never to whichever environment a Thread selects
        later. Its close callback must finish the task or raise for retry.
        """
        return await self._start_owned_task(lease, start, close, self._resource_tasks)

    async def _start_owned_task(self, lease, start, close, registry) -> asyncio.Task:
        async with self._lock:
            descriptor = self._record(lease.environment_id).descriptor
            if self._shutting_down or descriptor.state is not EnvironmentState.READY:
                raise EnvironmentUnavailableError(
                    lease.environment_id, descriptor.state.value
                )
            if descriptor.generation != lease.generation:
                raise EnvironmentChangedError(
                    lease.environment_id, lease.generation, descriptor.generation
                )
            task = start()
            tasks = registry.setdefault(lease.environment_id, {})
            tasks[task] = close

            def finished(done: asyncio.Task) -> None:
                if registry is self._resource_tasks and (
                    done.cancelled() or done.exception() is not None
                ):
                    # A failed owner task is not proof that its external
                    # resources closed. Keep its failure visible to stop.
                    return
                tasks.pop(done, None)
                if not tasks and registry.get(lease.environment_id) is tasks:
                    registry.pop(lease.environment_id, None)

            task.add_done_callback(finished)
            return task

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
            if self._shutting_down:
                raise EnvironmentProvisioningUnavailableError(workspace_id, profile_id)
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
            if self._shutting_down:
                raise RuntimeError("execution environment manager is shutting down")
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

    async def set_capability(
        self,
        environment_id: str,
        capability: EnvironmentCapability,
        *,
        present: bool,
    ) -> ExecutionEnvironmentDescriptor:
        """Grant or revoke one capability on the live descriptor.

        Same generation, same workspace, same runtime — this records a
        change in what the *current* backend can do (e.g. the network
        egress toggle), not a replacement. Existing leases keep their
        immutable snapshot; new acquisitions and ``describe`` (which the
        approval policy reads on every request) see the updated set.
        """
        if not isinstance(capability, EnvironmentCapability):
            raise TypeError("capability must be an EnvironmentCapability")
        async with self._lock:
            record = self._record(environment_id)
            capabilities = set(record.descriptor.capabilities)
            if present:
                capabilities.add(capability)
            else:
                capabilities.discard(capability)
            descriptor = replace(
                record.descriptor, capabilities=frozenset(capabilities)
            )
            self._environments[environment_id] = _EnvironmentRecord(
                descriptor,
                record.workspace,
                record.lifecycle_controller,
                record.sandbox_runtime,
                record.terminal_defaults,
            )
            return descriptor

    async def retire(self, environment_id: str) -> ExecutionEnvironmentDescriptor:
        """Remove one environment from routing (e.g. a worker's worktree
        after integration). Leases are immutable snapshots, so in-flight
        runs holding one are unaffected; new acquisitions fail with
        EnvironmentNotFoundError. Raises EnvironmentNotFoundError when the
        id is unknown."""
        async with self._lock:
            record = self._record(environment_id)
            del self._environments[environment_id]
            return record.descriptor

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
        """Join retirement; disconnecting a caller must not interrupt cleanup."""
        task = self._stop_tasks.get(environment_id)
        if task is None:
            task = asyncio.create_task(self._stop_environment(environment_id))
            self._stop_tasks[environment_id] = task

            def finished(done: asyncio.Task) -> None:
                if self._stop_tasks.get(environment_id) is done:
                    self._stop_tasks.pop(environment_id, None)
                if not done.cancelled():
                    done.exception()  # Observe failures even after caller disconnect.

            task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def _stop_environment(
        self, environment_id: str
    ) -> ExecutionEnvironmentDescriptor:
        closing_lease: EnvironmentLease
        async with self._lock:
            current = self._record(environment_id)
            descriptor = current.descriptor
            if descriptor.state not in {
                EnvironmentState.READY,
                EnvironmentState.STOPPING,
            }:
                raise EnvironmentUnavailableError(
                    environment_id, descriptor.state.value
                )
            closing_lease = EnvironmentLease(
                # A retry uses the same resources/generation as the original
                # ready lease. This private teardown snapshot never republishes
                # READY or permits a new acquire against the STOPPING record.
                replace(descriptor, state=EnvironmentState.READY),
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
            runs = tuple(self._run_tasks.get(environment_id, {}).items())
            resources = tuple(self._resource_tasks.get(environment_id, {}).items())

        close = getattr(closing_lease.lifecycle_controller, "close", None)
        workers_finished = False
        try:
            # STOPPING is already visible: no late start can escape this
            # snapshot. Finish run cleanup (including approval resolutions)
            # before closing the resources those workers still own.
            results = await asyncio.gather(
                *(cancel() for task, cancel in runs if not task.done()),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result
            if any(not task.done() for task, _ in runs):
                raise RuntimeError("environment runs did not finish cancellation")
            # MCP/other auxiliary workers can still be serving the cancelled
            # run's cleanup. Close them only once all run tasks have joined.
            results = await asyncio.gather(
                *(release() for _, release in resources),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result
            if any(not task.done() for task, _ in resources):
                raise RuntimeError("environment resource workers did not finish")
            workers_finished = True
            if callable(close):
                await close(closing_lease)
        except BaseException:
            async with self._lock:
                failed_current = self._record(environment_id)
                failed = replace(
                    failed_current.descriptor,
                    # A live run is not a retired environment. Keep starts
                    # fenced and allow a subsequent stop to retry cancellation.
                    state=(
                        EnvironmentState.FAILED
                        if workers_finished
                        else EnvironmentState.STOPPING
                    ),
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

    async def shutdown(self) -> tuple[ExecutionEnvironmentDescriptor, ...]:
        """Stop every ready environment owned by this AgentHost.

        Provisioning already in flight is allowed to finish first so a backend
        cannot be created after the shutdown inventory is captured. Every
        ready environment is then closed even if another close fails. Failures
        are reported together only after all remaining environments have been
        attempted.
        """

        async with self._lock:
            self._shutting_down = True
            provisioning_tasks = tuple(self._provisioning_tasks.values())
        if provisioning_tasks:
            await asyncio.gather(*provisioning_tasks, return_exceptions=True)

        async with self._lock:
            environment_ids = tuple(
                environment_id
                for environment_id, record in self._environments.items()
                if record.descriptor.state
                in {EnvironmentState.READY, EnvironmentState.STOPPING}
            )

        stopped: list[ExecutionEnvironmentDescriptor] = []
        failures: dict[str, Exception] = {}
        for environment_id in environment_ids:
            try:
                stopped.append(await self.stop(environment_id))
            except Exception as exc:  # noqa: BLE001 - continue closing peers
                failures[environment_id] = exc

        if failures:
            raise EnvironmentShutdownError(failures)
        return tuple(stopped)

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
