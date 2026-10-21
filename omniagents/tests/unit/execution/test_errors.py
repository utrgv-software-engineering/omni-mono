import pytest

from omniagents.execution import (
    CapabilityUnavailableError,
    EnvironmentChangedError,
    EnvironmentNotFoundError,
    EnvironmentRequiredError,
    EnvironmentUnavailableError,
    ExecutionResolutionError,
    WorkspaceEnvironmentMismatchError,
    WorkspaceNotFoundError,
)


@pytest.mark.parametrize(
    ("error", "kind", "data"),
    [
        (
            EnvironmentRequiredError("git_status"),
            "environment_required",
            {"operation": "git_status"},
        ),
        (
            EnvironmentNotFoundError("env-1"),
            "environment_not_found",
            {"environment_id": "env-1"},
        ),
        (
            EnvironmentUnavailableError("env-1", "stopped"),
            "environment_unavailable",
            {"environment_id": "env-1", "state": "stopped"},
        ),
        (
            EnvironmentChangedError("env-1", 2, 3),
            "environment_changed",
            {
                "environment_id": "env-1",
                "expected_generation": 2,
                "actual_generation": 3,
            },
        ),
        (
            CapabilityUnavailableError("env-1", {"process", "network"}),
            "capability_unavailable",
            {"environment_id": "env-1", "missing": ["network", "process"]},
        ),
        (
            WorkspaceNotFoundError("workspace-1"),
            "workspace_not_found",
            {"workspace_id": "workspace-1"},
        ),
        (
            WorkspaceEnvironmentMismatchError("workspace-1", "env-1", "workspace-2"),
            "workspace_environment_mismatch",
            {
                "workspace_id": "workspace-1",
                "environment_id": "env-1",
                "environment_workspace_id": "workspace-2",
            },
        ),
    ],
)
def test_execution_resolution_errors_have_stable_machine_readable_payloads(
    error: ExecutionResolutionError, kind: str, data: dict
):
    assert error.kind == kind
    assert error.data == {"kind": kind, **data}
    assert str(error)
