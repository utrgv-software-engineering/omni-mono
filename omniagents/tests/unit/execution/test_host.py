import pytest

from omniagents.execution import (
    AgentHostResources,
    EnvironmentKind,
    register_host_environment,
)


def test_agent_host_resources_share_one_explicit_composition_root():
    resources = AgentHostResources.create("agent-host-1")

    assert resources.agent_host_id == "agent-host-1"
    assert resources.router._workspace_registry is resources.workspaces
    assert resources.router._environment_manager is resources.environments


@pytest.mark.asyncio
async def test_host_execution_exists_only_after_explicit_registration(tmp_path):
    resources = AgentHostResources.create("agent-host-1")

    descriptor = register_host_environment(
        resources,
        str(tmp_path),
        owner_user_id="user-1",
        workspace_id="workspace-1",
        environment_id="environment-1",
    )

    lease = await resources.environments.acquire("environment-1")
    assert descriptor.kind is EnvironmentKind.HOST
    assert lease.workspace.root == str(tmp_path)
    assert resources.workspaces.get("workspace-1").owner_user_id == "user-1"


@pytest.mark.parametrize("agent_host_id", ["", "  "])
def test_agent_host_identity_is_required(agent_host_id):
    with pytest.raises(ValueError, match="non-empty"):
        AgentHostResources.create(agent_host_id)
