from __future__ import annotations

import time
from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from omniagents.builtin.server_functions.bash_jobs import bash_jobs_list
from omniagents.builtin.tools._bash_jobs import Job, get_registry
from omniagents.execution import (
    EnvironmentCapability,
    EnvironmentKind,
    EnvironmentLease,
    EnvironmentState,
    ExecutionEnvironmentDescriptor,
    OperationExecutionDescriptor,
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope
from omniagents.workspace.base import Workspace


class _Handle:
    pid = None


@contextmanager
def _routed(environment_id: str):
    descriptor = ExecutionEnvironmentDescriptor(
        environment_id=environment_id,
        workspace_id="workspace-1",
        kind=EnvironmentKind.DEVBOX,
        state=EnvironmentState.READY,
        generation=2,
        capabilities=frozenset({EnvironmentCapability.PROCESS}),
    )
    workspace = Mock(spec=Workspace)
    workspace.root = "/workspace"
    token = bind_request_context(
        RequestContext(
            operation=OperationExecutionDescriptor(
                name="bash_jobs.list",
                resource_scope=ResourceScope.THREAD,
                execution_requirements=ExecutionRequirement.PROCESS,
            ),
            principal=None,
            environment=descriptor,
            environment_lease=EnvironmentLease(descriptor, workspace),
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


@pytest.mark.asyncio
async def test_list_only_returns_jobs_from_routed_environment():
    now = time.monotonic()
    for suffix, environment_id in (
        ("selected", "env-selected"),
        ("other", "env-other"),
        ("untagged", None),
    ):
        get_registry().register(
            Job(
                job_id=f"routing-{suffix}",
                command="true",
                cwd="/workspace",
                log_path=f"/workspace/.omniagents/jobs/routing-{suffix}.log",
                started_at_wall=time.time(),
                started_at_monotonic=now,
                handle=_Handle(),  # type: ignore[arg-type]
                session_id="thread-1",
                environment_id=environment_id,
            )
        )

    with _routed("env-selected"):
        result = await bash_jobs_list(_session_id="thread-1")

    assert result["environment_id"] == "env-selected"
    assert [job["job_id"] for job in result["snapshot"]] == ["routing-selected"]
