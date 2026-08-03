from __future__ import annotations

from unittest.mock import Mock

import pytest

from omniagents.execution import (
    AutomaticEnvironment,
    EnvironmentCapability,
    EnvironmentKind,
    EnvironmentLease,
    EnvironmentState,
    ExistingEnvironment,
    ExecutionEnvironmentDescriptor,
    NoEnvironment,
    SandboxExecutionRuntime,
    TerminalExecutionDefaults,
    ThreadRuntimeBinding,
    WorkspaceResource,
)


def descriptor(
    *,
    environment_id: str = "env-1",
    workspace_id: str = "workspace-1",
    state: EnvironmentState = EnvironmentState.READY,
) -> ExecutionEnvironmentDescriptor:
    return ExecutionEnvironmentDescriptor(
        environment_id=environment_id,
        workspace_id=workspace_id,
        kind=EnvironmentKind.DEVBOX,
        state=state,
        generation=1,
        capabilities=frozenset(
            {
                EnvironmentCapability.WORKSPACE_READ,
                EnvironmentCapability.PROCESS,
            }
        ),
    )


def test_targetless_thread_binding_is_explicit():
    binding = ThreadRuntimeBinding()

    assert binding.workspace_id is None
    assert binding.environment_selection == NoEnvironment()
    assert binding.environment_id is None


def test_workspace_can_exist_without_a_live_environment():
    workspace = WorkspaceResource(
        "workspace-1",
        owner_user_id="user-1",
        snapshot_ref="snapshots/one.tar",
    )
    binding = ThreadRuntimeBinding(workspace_id=workspace.workspace_id)

    assert binding.workspace_id == "workspace-1"
    assert binding.environment_selection == NoEnvironment()
    assert workspace.owner_user_id == "user-1"


@pytest.mark.parametrize(
    "selection",
    [AutomaticEnvironment("devbox"), ExistingEnvironment("env-1")],
)
def test_environment_selection_requires_workspace(selection):
    with pytest.raises(ValueError, match="requires workspace_id"):
        ThreadRuntimeBinding(environment_selection=selection)


def test_explicit_environment_must_belong_to_bound_workspace():
    binding = ThreadRuntimeBinding(
        workspace_id="workspace-1",
        environment_selection=ExistingEnvironment("env-1"),
    )

    binding.validate_environment(descriptor())

    with pytest.raises(ValueError, match="different workspace"):
        binding.validate_environment(descriptor(workspace_id="workspace-2"))

    with pytest.raises(ValueError, match="selected environment_id"):
        binding.validate_environment(descriptor(environment_id="env-2"))


def test_environment_lease_requires_ready_generation_and_capabilities():
    session = object()
    lease = EnvironmentLease(
        descriptor(),
        Mock(),
        sandbox_runtime=SandboxExecutionRuntime(session, run_as="1000:1000"),
    )

    assert lease.environment_id == "env-1"
    assert lease.workspace_id == "workspace-1"
    assert lease.generation == 1
    assert lease.sandbox_runtime.session is session
    assert lease.sandbox_runtime.run_as == "1000:1000"
    lease.require(EnvironmentCapability.WORKSPACE_READ)

    with pytest.raises(ValueError, match="workspace_write"):
        lease.require(EnvironmentCapability.WORKSPACE_WRITE)

    with pytest.raises(ValueError, match="ready environment"):
        EnvironmentLease(descriptor(state=EnvironmentState.PROVISIONING), Mock())


def test_sandbox_runtime_requires_a_session_and_valid_run_as():
    with pytest.raises(ValueError, match="requires a session"):
        SandboxExecutionRuntime(None)

    with pytest.raises(ValueError, match="run_as must be a non-empty string"):
        SandboxExecutionRuntime(object(), run_as=" ")


def test_terminal_defaults_reject_blank_values():
    with pytest.raises(ValueError, match="shell must be a non-empty string"):
        TerminalExecutionDefaults(shell="")


@pytest.mark.parametrize("identifier", ["", "  "])
def test_resource_ids_must_be_non_empty(identifier):
    with pytest.raises(ValueError, match="non-empty"):
        WorkspaceResource(identifier)


def test_generation_is_a_positive_integer():
    with pytest.raises(ValueError, match="at least 1"):
        ExecutionEnvironmentDescriptor(
            environment_id="env-1",
            workspace_id="workspace-1",
            kind=EnvironmentKind.HOST,
            state=EnvironmentState.READY,
            generation=0,
        )

    with pytest.raises(TypeError, match="integer"):
        ExecutionEnvironmentDescriptor(
            environment_id="env-1",
            workspace_id="workspace-1",
            kind=EnvironmentKind.HOST,
            state=EnvironmentState.READY,
            generation=True,
        )
