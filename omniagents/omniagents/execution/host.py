"""AgentHost-owned execution resources and their explicit composition root."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import uuid

from omniagents.workspace import HostWorkspace

from .control import AgentHostProvisioningCatalog
from .errors import WorkspaceNotFoundError
from .models import (
    EnvironmentCapability,
    EnvironmentKind,
    EnvironmentState,
    ExecutionEnvironmentDescriptor,
    SandboxExecutionRuntime,
    WorkspaceResource,
)
from .provisioning import EnvironmentProvisioner
from .registry import (
    ExecutionEnvironmentManager,
    ThreadRuntimeBindingRegistry,
    WorkspaceRegistry,
)
from .routing import OperationRouter

_FULL_CAPABILITIES = frozenset(
    {
        EnvironmentCapability.WORKSPACE_READ,
        EnvironmentCapability.WORKSPACE_WRITE,
        EnvironmentCapability.PROCESS,
        EnvironmentCapability.NETWORK,
        EnvironmentCapability.PTY,
        EnvironmentCapability.HTTP,
        EnvironmentCapability.SERVICES,
        EnvironmentCapability.ENVIRONMENT_LIFECYCLE,
    }
)


@dataclass(frozen=True, slots=True)
class AgentHostResources:
    """Long-lived registries and router shared by one AgentHost.

    Threads and tabs may reference these resources but do not own them. The
    bundle is created once by a product composition root and passed to every
    AgentService hosted in that process.
    """

    agent_host_id: str
    workspaces: WorkspaceRegistry
    environments: ExecutionEnvironmentManager
    thread_bindings: ThreadRuntimeBindingRegistry
    router: OperationRouter
    provisioning: AgentHostProvisioningCatalog

    @classmethod
    def create(
        cls,
        agent_host_id: str,
        *,
        environment_provisioner: EnvironmentProvisioner | None = None,
    ) -> AgentHostResources:
        if not isinstance(agent_host_id, str) or not agent_host_id.strip():
            raise ValueError("agent_host_id must be a non-empty string")
        workspaces = WorkspaceRegistry()
        environments = ExecutionEnvironmentManager(
            workspaces, provisioner=environment_provisioner
        )
        thread_bindings = ThreadRuntimeBindingRegistry(workspaces, environments)
        provisioning = AgentHostProvisioningCatalog(workspaces)
        router = OperationRouter(
            workspaces,
            environments,
            thread_bindings,
            agent_host_id=agent_host_id,
        )
        return cls(
            agent_host_id,
            workspaces,
            environments,
            thread_bindings,
            router,
            provisioning,
        )


def register_host_environment(
    resources: AgentHostResources,
    root: str,
    *,
    owner_user_id: str | None,
    workspace_id: str | None = None,
    environment_id: str | None = None,
) -> ExecutionEnvironmentDescriptor:
    """Compose an explicit host environment before the AgentHost starts."""

    workspace_id = workspace_id or f"workspace_{uuid.uuid4().hex}"
    environment_id = environment_id or f"environment_{uuid.uuid4().hex}"
    # Unconfined, like every host environment — this is the eager composition
    # root (ink/web/studio/worker worktrees); ``ServeEnvironmentProvisioner``
    # is the lazy one. The two must agree on what a host target is, and
    # HostWorkspace no longer offers a fenced variant to disagree with.
    workspace = HostWorkspace(root=root)
    try:
        existing = resources.workspaces.get(workspace_id)
    except WorkspaceNotFoundError:
        resources.workspaces.register(
            WorkspaceResource(workspace_id, owner_user_id=owner_user_id)
        )
    else:
        if existing.owner_user_id != owner_user_id:
            raise PermissionError("Workspace owner changed")
    descriptor = ExecutionEnvironmentDescriptor(
        environment_id=environment_id,
        workspace_id=workspace_id,
        kind=EnvironmentKind.HOST,
        state=EnvironmentState.READY,
        generation=1,
        capabilities=_FULL_CAPABILITIES,
    )
    resources.environments.register_initial(descriptor, workspace)
    return descriptor


def register_sandbox_environment(
    resources: AgentHostResources,
    session: Any,
    root: str,
    *,
    owner_user_id: str | None,
    capabilities: frozenset[EnvironmentCapability],
    run_as: str | None = None,
    workspace_id: str | None = None,
    environment_id: str | None = None,
) -> ExecutionEnvironmentDescriptor:
    """Compose an explicit sandbox environment around a started SDK session.

    The counterpart of :func:`register_host_environment` for a container:
    file tools go through a :class:`SandboxWorkspace` on ``session`` and
    runs get the session as their SDK sandbox runtime, as ``omni serve``
    registers a profile's sandbox. ``capabilities`` is the environment's
    truth (``contained`` above all, which the approval policy keys on); a
    profile's come from
    :func:`omniagents.core.runtime.environment_provisioner.sandbox_capabilities`.
    """

    from omniagents.workspace.sandbox import SandboxWorkspace

    workspace_id = workspace_id or f"workspace_{uuid.uuid4().hex}"
    environment_id = environment_id or f"environment_{uuid.uuid4().hex}"
    resources.workspaces.register(
        WorkspaceResource(workspace_id, owner_user_id=owner_user_id)
    )
    descriptor = ExecutionEnvironmentDescriptor(
        environment_id=environment_id,
        workspace_id=workspace_id,
        kind=EnvironmentKind.CONTAINER,
        state=EnvironmentState.READY,
        generation=1,
        capabilities=capabilities,
    )
    resources.environments.register_initial(
        descriptor,
        SandboxWorkspace(session=session, root=root, default_user=run_as),
        sandbox_runtime=SandboxExecutionRuntime(session=session, run_as=run_as),
    )
    return descriptor
