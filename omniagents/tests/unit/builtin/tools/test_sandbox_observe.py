"""Tests for environment-routed read-only sandbox observation tools."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

from omniagents.builtin.tools.sandbox_observe import (
    _state_dict,
    sandbox_get_state,
    sandbox_wait_until_ready,
)
from omniagents.execution import (
    EnvironmentKind,
    EnvironmentLease,
    EnvironmentState,
    ExecutionEnvironmentDescriptor,
    OperationExecutionDescriptor,
    RequestContext,
    WorkspaceResource,
    bind_request_context,
    reset_request_context,
)
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope


class _Controller:
    def __init__(self, profile="devbox", backend="docker"):
        self.profile = profile
        self.backend = backend
        self.switching = False
        self.ready = asyncio.Event()
        self.ready.set()

    def state(self, lease):
        assert lease.environment_id == "environment-1"
        return {
            "available": True,
            "ready": self.ready.is_set() and not self.switching,
            "switching": self.switching,
            "profile": self.profile,
            "backend": self.backend,
            "container_id": "ctr-1",
            "last_error": None,
        }

    async def wait_until_ready(self, lease, timeout_seconds):
        assert lease.environment_id == "environment-1"
        try:
            await asyncio.wait_for(self.ready.wait(), timeout_seconds)
        except asyncio.TimeoutError:
            return {"ready": False, "profile": self.profile, "reason": "timeout"}
        return {
            "ready": not self.switching,
            "profile": self.profile,
            "switching": self.switching,
            "last_error": None,
        }


@contextmanager
def _routed(controller):
    workspace_resource = WorkspaceResource("workspace-1")
    descriptor = ExecutionEnvironmentDescriptor(
        environment_id="environment-1",
        workspace_id=workspace_resource.workspace_id,
        kind=EnvironmentKind.CONTAINER,
        state=EnvironmentState.READY,
        generation=1,
    )
    workspace = type("Workspace", (), {"root": "/workspace"})()
    lease = EnvironmentLease(descriptor, workspace, lifecycle_controller=controller)
    token = bind_request_context(
        RequestContext(
            operation=OperationExecutionDescriptor(
                name="sandbox_get_state",
                resource_scope=ResourceScope.RUN,
                execution_requirements=ExecutionRequirement.ENVIRONMENT_LIFECYCLE,
            ),
            principal=None,
            workspace=workspace_resource,
            environment=descriptor,
            environment_lease=lease,
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


def test_state_dict_without_environment_reports_ready_unavailable():
    assert _state_dict() == {
        "available": False,
        "ready": True,
        "switching": False,
        "profile": None,
        "backend": None,
        "container_id": None,
        "last_error": None,
    }


def test_state_dict_reads_routed_environment_controller():
    with _routed(_Controller()):
        state = _state_dict()
    assert state["available"] is True and state["ready"] is True
    assert state["profile"] == "devbox" and state["backend"] == "docker"
    assert state["container_id"] == "ctr-1" and state["switching"] is False


def test_state_dict_mid_switch_not_ready():
    controller = _Controller()
    controller.switching = True
    controller.ready.clear()
    with _routed(controller):
        state = _state_dict()
    assert state["ready"] is False and state["switching"] is True


def test_tools_are_function_tools_with_stable_names():
    assert sandbox_get_state.name == "sandbox_get_state"
    assert sandbox_wait_until_ready.name == "sandbox_wait_until_ready"


def test_wait_until_ready_uses_routed_controller():
    async def go():
        controller = _Controller()
        controller.switching = True
        controller.ready.clear()
        with _routed(controller):
            pending = asyncio.create_task(
                sandbox_wait_until_ready._original_func(
                    SimpleNamespace(context={}), timeout_seconds=2.0
                )
            )
            await asyncio.sleep(0)
            controller.switching = False
            controller.ready.set()
            return json.loads(await pending)

    state = asyncio.run(go())
    assert state["ready"] is True and state["switching"] is False


def test_wait_until_ready_without_environment_is_immediate():
    async def go():
        return json.loads(
            await sandbox_wait_until_ready._original_func(
                SimpleNamespace(context={}), timeout_seconds=2.0
            )
        )

    state = asyncio.run(go())
    assert state == {
        "ready": True,
        "profile": None,
        "reason": "no execution environment",
    }
