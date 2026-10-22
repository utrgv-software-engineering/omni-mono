import asyncio

import pytest

from omniagents.execution import (
    AgentHostResources,
    EnvironmentChangedError,
    ExistingEnvironment,
    ThreadRuntimeBinding,
    WorkspaceEnvironmentMismatchError,
    WorkspaceNotFoundError,
    register_host_environment,
)


@pytest.mark.asyncio
async def test_thread_binding_registers_workspace_without_environment(tmp_path):
    resources = AgentHostResources.create("host-1")
    environment = register_host_environment(
        resources,
        str(tmp_path),
        owner_user_id=None,
        workspace_id="workspace-1",
        environment_id="environment-1",
    )
    binding = ThreadRuntimeBinding(workspace_id=environment.workspace_id)

    await resources.thread_bindings.register("thread-1", binding)

    assert await resources.thread_bindings.get("thread-1") == binding


@pytest.mark.asyncio
async def test_ensure_atomically_initializes_targetless_binding():
    resources = AgentHostResources.create("host-1")

    first, second = await asyncio.gather(
        resources.thread_bindings.ensure("thread-targetless"),
        resources.thread_bindings.ensure("thread-targetless"),
    )

    assert first == ThreadRuntimeBinding()
    assert second is first


@pytest.mark.asyncio
async def test_ensure_initializes_the_composed_default_binding(tmp_path):
    resources = AgentHostResources.create("host-1")
    environment = register_host_environment(
        resources,
        str(tmp_path),
        owner_user_id=None,
        workspace_id="workspace-1",
        environment_id="environment-1",
    )
    resources.thread_bindings.set_initial_default(environment)

    binding = await resources.thread_bindings.ensure("thread-new")

    assert binding == ThreadRuntimeBinding(
        workspace_id="workspace-1",
        environment_selection=ExistingEnvironment("environment-1"),
    )
    # An explicit binding still wins and is never reset by ensure.
    await resources.thread_bindings.bind("thread-none", ThreadRuntimeBinding())
    assert await resources.thread_bindings.ensure("thread-none") == (
        ThreadRuntimeBinding()
    )


def test_default_binding_requires_a_registered_environment(tmp_path):
    resources = AgentHostResources.create("host-1")
    other = AgentHostResources.create("host-2")
    environment = register_host_environment(
        other, str(tmp_path), owner_user_id=None
    )

    with pytest.raises(WorkspaceNotFoundError):
        resources.thread_bindings.set_initial_default(environment)


@pytest.mark.asyncio
async def test_failed_rebind_preserves_atomic_workspace_environment_pair(tmp_path):
    resources = AgentHostResources.create("host-1")
    first = register_host_environment(
        resources,
        str(tmp_path / "first"),
        owner_user_id=None,
        workspace_id="workspace-1",
        environment_id="environment-1",
    )
    second = register_host_environment(
        resources,
        str(tmp_path / "second"),
        owner_user_id=None,
        workspace_id="workspace-2",
        environment_id="environment-2",
    )
    original = ThreadRuntimeBinding(
        workspace_id=first.workspace_id,
        environment_selection=ExistingEnvironment(first.environment_id),
    )
    await resources.thread_bindings.register("thread-1", original)

    mismatched = ThreadRuntimeBinding(
        workspace_id=first.workspace_id,
        environment_selection=ExistingEnvironment(second.environment_id),
    )
    with pytest.raises(WorkspaceEnvironmentMismatchError):
        await resources.thread_bindings.rebind("thread-1", mismatched)

    assert await resources.thread_bindings.get("thread-1") == original


@pytest.mark.asyncio
async def test_binding_rejects_unknown_workspace():
    resources = AgentHostResources.create("host-1")

    with pytest.raises(WorkspaceNotFoundError):
        await resources.thread_bindings.register(
            "thread-1", ThreadRuntimeBinding(workspace_id="missing")
        )


@pytest.mark.asyncio
async def test_existing_binding_generation_mismatch_fails_closed(tmp_path):
    resources = AgentHostResources.create("host-1")
    environment = register_host_environment(
        resources,
        str(tmp_path),
        owner_user_id=None,
        workspace_id="workspace-1",
        environment_id="environment-1",
    )
    pinned = ThreadRuntimeBinding(
        workspace_id=environment.workspace_id,
        environment_selection=ExistingEnvironment(
            environment.environment_id, environment.generation
        ),
    )
    await resources.thread_bindings.bind("thread-1", pinned)

    lease = await resources.environments.acquire(environment.environment_id)
    replaced = await resources.environments.replace(
        environment.environment_id, lease.workspace
    )

    with pytest.raises(EnvironmentChangedError):
        await resources.thread_bindings.bind("thread-1", pinned)

    rebound = ThreadRuntimeBinding(
        workspace_id=environment.workspace_id,
        environment_selection=ExistingEnvironment(
            environment.environment_id, replaced.generation
        ),
    )
    await resources.thread_bindings.bind("thread-1", rebound)

    assert await resources.thread_bindings.get("thread-1") == rebound
