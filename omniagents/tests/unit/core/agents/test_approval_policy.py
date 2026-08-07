from types import SimpleNamespace

import pytest

from omniagents.core.agents.approval_policy import (
    ApprovalRoute,
    EnvironmentSummary,
    HOST_ENVIRONMENT,
    resolve_environment_summary,
    resolve_route,
)


def env(kind, *capabilities):
    return EnvironmentSummary(kind=kind, capabilities=frozenset(capabilities))


@pytest.mark.parametrize(
    ("environment", "reviewer", "tier", "expected"),
    [
        # Host never auto-approves, whatever the tier claims.
        (env("host"), "user", "always", ApprovalRoute.HUMAN),
        (env("host"), "auto", "always", ApprovalRoute.GUARDIAN),
        (HOST_ENVIRONMENT, "user", "always", ApprovalRoute.HUMAN),
        # Offline sandbox auto-approves under the default tier.
        (env("devbox"), "user", "no_network", ApprovalRoute.AUTO_APPROVE),
        (env("devbox"), "auto", "no_network", ApprovalRoute.AUTO_APPROVE),
        # Networked sandbox does not auto-approve under no_network.
        (env("devbox", "network"), "user", "no_network", ApprovalRoute.HUMAN),
        (env("devbox", "network"), "auto", "no_network", ApprovalRoute.GUARDIAN),
        # 'always' covers networked sandboxes too; 'never' disables the tier.
        (env("devbox", "network"), "user", "always", ApprovalRoute.AUTO_APPROVE),
        (env("devbox"), "user", "never", ApprovalRoute.HUMAN),
        (env("devbox"), "auto", "never", ApprovalRoute.GUARDIAN),
    ],
)
def test_route_matrix(environment, reviewer, tier, expected):
    assert (
        resolve_route(reviewer=reviewer, sandbox_tier=tier, environment=environment)
        is expected
    )


@pytest.mark.asyncio
async def test_environment_summary_fails_closed_to_host():
    class NoBinding:
        async def get(self, thread_id):
            raise KeyError(thread_id)

    service = SimpleNamespace(thread_bindings=NoBinding(), environment_manager=None)
    session = SimpleNamespace(id="s-1")

    summary = await resolve_environment_summary(service, session)

    assert summary == HOST_ENVIRONMENT
    assert not summary.is_sandboxed


@pytest.mark.asyncio
async def test_environment_summary_reads_live_descriptor(tmp_path):
    from omniagents.execution import (
        AgentHostResources,
        ExistingEnvironment,
        ThreadRuntimeBinding,
        register_host_environment,
    )

    resources = AgentHostResources.create("host-1")
    descriptor = register_host_environment(
        resources,
        str(tmp_path),
        owner_user_id=None,
        workspace_id="workspace-1",
        environment_id="environment-1",
    )
    await resources.thread_bindings.bind(
        "thread-1",
        ThreadRuntimeBinding(
            workspace_id=descriptor.workspace_id,
            environment_selection=ExistingEnvironment(descriptor.environment_id),
        ),
    )
    service = SimpleNamespace(
        thread_bindings=resources.thread_bindings,
        environment_manager=resources.environments,
    )
    session = SimpleNamespace(id="thread-1")

    summary = await resolve_environment_summary(service, session)

    assert summary.kind == "host"
    assert summary.environment_id == "environment-1"
    assert "workspace_read" in summary.capabilities
