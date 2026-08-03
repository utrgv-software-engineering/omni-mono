from types import SimpleNamespace

import pytest

from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.providers.auth.base import UserIdentity
from omniagents.execution import AgentHostResources, AutomaticEnvironment
from omniagents.rpc.auth import AuthorizationDenied, AuthorizationPolicy


def make_service() -> AgentService:
    spec = AgentSpec(
        name="control",
        welcome_text="",
        get_agent_instructions=lambda: "",
        build_agent=None,
    )
    return AgentService(spec, AgentHostResources.create("control-host"))


@pytest.mark.asyncio
async def test_control_plane_registers_and_binds_without_provisioning(tmp_path):
    service = make_service()

    workspace = await service.agent_host_register_workspace(
        "workspace-1", str(tmp_path)
    )
    profile = await service.agent_host_register_profile(
        "devbox", {"kind": "default", "project_id": "project-1"}
    )
    binding = await service.agent_host_bind_thread(
        "thread-1",
        {
            "workspace_id": "workspace-1",
            "environment_selection": {
                "mode": "automatic",
                "profile_id": "devbox",
            },
        },
    )
    resources = await service.agent_host_list_resources()

    assert workspace["workspace_id"] == "workspace-1"
    assert profile["definition"]["kind"] == "default"
    assert binding["environment_selection"] == {
        "mode": "automatic",
        "profile_id": "devbox",
    }
    assert isinstance(
        (await service.thread_bindings.get("thread-1")).environment_selection,
        AutomaticEnvironment,
    )
    assert resources["agent_host_id"] == "control-host"
    assert [item["workspace_id"] for item in resources["workspaces"]] == ["workspace-1"]
    assert resources["profiles"] == {
        "devbox": {"kind": "default", "project_id": "project-1"}
    }
    assert resources["environments"] == []


@pytest.mark.asyncio
async def test_host_path_registration_requires_agent_host_admin_in_enforced_mode():
    service = make_service()
    service.authorization_policy = AuthorizationPolicy(enforced=True)

    with pytest.raises(AuthorizationDenied, match="administrator"):
        await service.authorize_rpc(
            UserIdentity(user_id="alice"),
            "agent_host_register_workspace",
            {"workspace_id": "workspace-1", "materialization_path": "/repo"},
        )

    await service.authorize_rpc(
        UserIdentity(user_id="operator", roles=["admin"]),
        "agent_host_register_workspace",
        {"workspace_id": "workspace-1", "materialization_path": "/repo"},
    )


@pytest.mark.asyncio
async def test_admin_registration_can_assign_resources_to_client_owner(tmp_path):
    service = make_service()
    service.authorization_policy = AuthorizationPolicy(enforced=True)
    service.channel = SimpleNamespace(
        principal=UserIdentity(user_id="agent_host_control", roles=["admin"])
    )

    workspace = await service.agent_host_register_workspace(
        "workspace-client",
        str(tmp_path),
        owner_user_id="token_user",
    )
    profile = await service.agent_host_register_profile(
        "devbox",
        {"kind": "default"},
        owner_user_id="token_user",
    )

    assert workspace["owner_user_id"] == "token_user"
    assert profile["definition"] == {"kind": "default"}


@pytest.mark.asyncio
async def test_thread_binding_cannot_cross_workspace_ownership(tmp_path):
    service = make_service()
    service.authorization_policy = AuthorizationPolicy(enforced=True)
    service.channel = SimpleNamespace(principal=UserIdentity(user_id="alice"))
    service.agent_host_resources.provisioning.register_workspace(
        workspace_id="workspace-bob",
        owner_user_id="bob",
        materialization_path=str(tmp_path),
    )

    with pytest.raises(AuthorizationDenied, match="Workspace"):
        await service.agent_host_bind_thread(
            "thread-alice",
            {
                "workspace_id": "workspace-bob",
                "environment_selection": {"mode": "none"},
            },
        )


@pytest.mark.asyncio
async def test_resource_listing_is_scoped_to_current_owner(tmp_path):
    service = make_service()
    service.authorization_policy = AuthorizationPolicy(enforced=True)
    for owner in ("alice", "bob"):
        service.agent_host_resources.provisioning.register_workspace(
            workspace_id=f"workspace-{owner}",
            owner_user_id=owner,
            materialization_path=str(tmp_path / owner),
        )
        service.agent_host_resources.provisioning.register_profile(
            "default", {"kind": "default"}, owner_user_id=owner
        )

    service.channel = SimpleNamespace(principal=UserIdentity(user_id="alice"))
    resources = await service.agent_host_list_resources()

    assert [item["workspace_id"] for item in resources["workspaces"]] == [
        "workspace-alice"
    ]
    assert resources["profiles"] == {"default": {"kind": "default"}}


@pytest.mark.asyncio
async def test_automatic_binding_requires_registered_profile(tmp_path):
    service = make_service()
    await service.agent_host_register_workspace("workspace-1", str(tmp_path))

    with pytest.raises(KeyError, match="not registered"):
        await service.agent_host_bind_thread(
            "thread-1",
            {
                "workspace_id": "workspace-1",
                "environment_selection": {
                    "mode": "automatic",
                    "profile_id": "missing",
                },
            },
        )
