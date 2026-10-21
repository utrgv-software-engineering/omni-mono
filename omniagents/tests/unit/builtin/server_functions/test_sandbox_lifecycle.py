"""Tests for builtin ``sandbox.*`` lifecycle functions and capability pack."""

from __future__ import annotations

import asyncio
import textwrap
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest

from omniagents.builtin.server_functions.sandbox_lifecycle import (
    SANDBOX_SERVER_FUNCTIONS,
    sandbox_discard_snapshot,
    sandbox_pause,
    sandbox_unpause,
)
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
from omniagents.execution.errors import CapabilityUnavailableError
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope
from omniagents.workspace.base import Workspace


class _Controller:
    def __init__(self):
        self.calls = []

    async def pause(self, lease):
        self.calls.append(("pause", lease.environment_id))
        return {"ok": True, "supported": True, "paused": True}

    async def unpause(self, lease):
        self.calls.append(("unpause", lease.environment_id))
        return {"ok": True, "supported": True, "paused": False}

    async def discard_snapshot(self, lease):
        self.calls.append(("discard", lease.environment_id))
        return {"ok": True}


@contextmanager
def _routed(controller):
    descriptor = ExecutionEnvironmentDescriptor(
        environment_id="env-1",
        workspace_id="workspace-1",
        kind=EnvironmentKind.CONTAINER,
        state=EnvironmentState.READY,
        generation=4,
        capabilities=frozenset({EnvironmentCapability.ENVIRONMENT_LIFECYCLE}),
    )
    workspace = Mock(spec=Workspace)
    workspace.root = "/workspace"
    lease = EnvironmentLease(descriptor, workspace, controller)
    operation = OperationExecutionDescriptor(
        name="sandbox.test",
        resource_scope=ResourceScope.ENVIRONMENT,
        execution_requirements=ExecutionRequirement.ENVIRONMENT_LIFECYCLE,
    )
    token = bind_request_context(
        RequestContext(
            operation=operation,
            principal=None,
            environment=descriptor,
            environment_lease=lease,
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


# ----------------------------- pause / unpause -------------------------- #


def test_pause_unpause_happy_path():
    controller = _Controller()
    with _routed(controller):
        assert asyncio.run(sandbox_pause(None))["paused"] is True
        assert asyncio.run(sandbox_unpause(None))["paused"] is False
    assert controller.calls == [("pause", "env-1"), ("unpause", "env-1")]


def test_lifecycle_without_controller_is_typed_capability_error():
    with _routed(None), pytest.raises(CapabilityUnavailableError):
        asyncio.run(sandbox_pause(None))


# ----------------------------- set/get network -------------------------- #


class _NetworkController(_Controller):
    def __init__(self, *, supported=True, enabled=True, ok=True):
        super().__init__()
        self.supported = supported
        self.enabled = enabled
        self.ok = ok

    async def set_network(self, lease, *, enabled):
        self.calls.append(("set_network", lease.environment_id, enabled))
        if not self.supported:
            return {"ok": False, "supported": False, "reason": "unsupported"}
        if not self.ok:
            return {"ok": False, "supported": True, "reason": "iptables failed"}
        self.enabled = enabled
        return {"ok": True, "supported": True, "enabled": enabled}

    async def get_network(self, lease):
        self.calls.append(("get_network", lease.environment_id))
        return {"ok": True, "supported": self.supported, "enabled": self.enabled}


class _RecordingEnvironmentManager:
    def __init__(self):
        self.capability_calls = []

    async def set_capability(self, environment_id, capability, *, present):
        self.capability_calls.append((environment_id, capability, present))


def _service_with_manager():
    service = Mock()
    service.environment_manager = _RecordingEnvironmentManager()
    return service


def test_set_network_flips_capability_on_success():
    from omniagents.builtin.server_functions.sandbox_lifecycle import (
        sandbox_set_network,
    )

    controller = _NetworkController()
    service = _service_with_manager()
    with _routed(controller):
        result = asyncio.run(sandbox_set_network(service, enabled=False))
    assert result == {"ok": True, "supported": True, "enabled": False}
    assert service.environment_manager.capability_calls == [
        ("env-1", EnvironmentCapability.NETWORK, False)
    ]
    with _routed(controller):
        result = asyncio.run(sandbox_set_network(service, enabled=True))
    assert result["enabled"] is True
    assert service.environment_manager.capability_calls[-1] == (
        "env-1",
        EnvironmentCapability.NETWORK,
        True,
    )


def test_set_network_failure_leaves_capability_untouched():
    from omniagents.builtin.server_functions.sandbox_lifecycle import (
        sandbox_set_network,
    )

    controller = _NetworkController(ok=False)
    service = _service_with_manager()
    with _routed(controller):
        result = asyncio.run(sandbox_set_network(service, enabled=False))
    assert result["ok"] is False
    assert service.environment_manager.capability_calls == []


def test_set_network_unsupported_backend_reports_unsupported():
    from omniagents.builtin.server_functions.sandbox_lifecycle import (
        sandbox_set_network,
    )

    controller = _NetworkController(supported=False)
    service = _service_with_manager()
    with _routed(controller):
        result = asyncio.run(sandbox_set_network(service, enabled=False))
    assert result == {"ok": False, "supported": False, "reason": "unsupported"}
    assert service.environment_manager.capability_calls == []


def test_get_network_reports_state():
    from omniagents.builtin.server_functions.sandbox_lifecycle import (
        sandbox_get_network,
    )

    controller = _NetworkController(enabled=False)
    with _routed(controller):
        result = asyncio.run(sandbox_get_network(None))
    assert result == {"ok": True, "supported": True, "enabled": False}


# ----------------------------- discard_snapshot ------------------------- #


def test_discard_snapshot_sets_flag():
    controller = _Controller()
    with _routed(controller):
        assert asyncio.run(sandbox_discard_snapshot(None)) == {"ok": True}
    assert controller.calls == [("discard", "env-1")]


# ----------------------- pack surface via load_project ------------------ #


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content))


def test_sandbox_pack_rpc_surface(tmp_path):
    from omniagents.core.project.runtime import load_project

    _write(tmp_path / "project.yml", "name: test\n")
    _write(
        tmp_path / "agents" / "main" / "agent.yml",
        """
        name: main
        instructions: be a test
        capabilities:
          - sandbox
        """,
    )

    proj = load_project(str(tmp_path))
    assert "sandbox" in proj.capability_registry
    cap = proj.capability_registry["sandbox"]

    tool_names = {t.name for t in cap.tools()}
    assert tool_names == {"sandbox_get_state", "sandbox_wait_until_ready"}

    sf_names = {m["name"] for m in cap.server_functions()}
    assert sf_names == {
        "sandbox.pause",
        "sandbox.unpause",
        "sandbox.discard_snapshot",
        "sandbox.set_network",
        "sandbox.get_network",
    }

    instructions = asyncio.run(cap.instructions())
    assert "You CANNOT change" in instructions
    assert "sandbox_wait_until_ready" in instructions

    # The builtin server-function registry carries the RPC names.
    assert "sandbox.pause" in proj.server_functions_registry
    assert len(SANDBOX_SERVER_FUNCTIONS) == 5
