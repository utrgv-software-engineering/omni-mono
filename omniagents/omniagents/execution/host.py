"""AgentHost-owned execution resources and their explicit composition root."""

from __future__ import annotations

from dataclasses import dataclass
import uuid

from omniagents.workspace import HostWorkspace

from .models import (
    EnvironmentCapability,
    EnvironmentKind,
    EnvironmentState,
    ExecutionEnvironmentDescriptor,
    WorkspaceResource,
)
from .registry import (
    ExecutionEnvironmentManager,
    ThreadRuntimeBindingRegistry,
    WorkspaceRegistry,
)
from .routing import OperationRouter


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

    @classmethod
    def create(cls, agent_host_id: str) -> AgentHostResources:
        if not isinstance(agent_host_id, str) or not agent_host_id.strip():
            raise ValueError("agent_host_id must be a non-empty string")
        workspaces = WorkspaceRegistry()
        environments = ExecutionEnvironmentManager(workspaces)
        thread_bindings = ThreadRuntimeBindingRegistry(workspaces, environments)
        router = OperationRouter(
            workspaces,
            environments,
            thread_bindings,
            agent_host_id=agent_host_id,
        )
        return cls(agent_host_id, workspaces, environments, thread_bindings, router)


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
    workspace = HostWorkspace(root=root)
    resources.workspaces.register(
        WorkspaceResource(workspace_id, owner_user_id=owner_user_id)
    )
    descriptor = ExecutionEnvironmentDescriptor(
        environment_id=environment_id,
        workspace_id=workspace_id,
        kind=EnvironmentKind.HOST,
        state=EnvironmentState.READY,
        generation=1,
        capabilities=frozenset(
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
        ),
    )
    resources.environments.register_initial(descriptor, workspace)
    return descriptor
