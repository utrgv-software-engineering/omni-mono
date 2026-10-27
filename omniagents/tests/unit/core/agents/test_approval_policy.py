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
        # The reviewer choice is authoritative: "Ask me first" always
        # prompts, even in an offline contained sandbox — no environment
        # state overrides the user's approval policy.
        (
            env("container", "contained"),
            "user",
            "no_network",
            ApprovalRoute.HUMAN,
        ),
        # Within "Approve for me", an offline CONTAINED sandbox upgrades
        # guardian review to promptless under the default tier.
        (
            env("container", "contained"),
            "auto",
            "no_network",
            ApprovalRoute.AUTO_APPROVE,
        ),
        # Networked contained sandbox does not auto-approve under no_network.
        (
            env("container", "contained", "network"),
            "user",
            "no_network",
            ApprovalRoute.HUMAN,
        ),
        (
            env("container", "contained", "network"),
            "auto",
            "no_network",
            ApprovalRoute.GUARDIAN,
        ),
        # 'always' covers networked contained sandboxes too — still only
        # within "Approve for me"; 'never' disables the tier.
        (
            env("container", "contained", "network"),
            "user",
            "always",
            ApprovalRoute.HUMAN,
        ),
        (
            env("container", "contained", "network"),
            "auto",
            "always",
            ApprovalRoute.AUTO_APPROVE,
        ),
        (env("container", "contained"), "user", "never", ApprovalRoute.HUMAN),
        (env("container", "contained"), "auto", "never", ApprovalRoute.GUARDIAN),
        # kind "container" WITHOUT the contained capability (unix_local —
        # sandbox sessions over raw host processes) never
        # auto-approves, even under 'always'.
        (env("container"), "user", "always", ApprovalRoute.HUMAN),
        (env("container"), "auto", "always", ApprovalRoute.GUARDIAN),
        (env("container"), "user", "no_network", ApprovalRoute.HUMAN),
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
