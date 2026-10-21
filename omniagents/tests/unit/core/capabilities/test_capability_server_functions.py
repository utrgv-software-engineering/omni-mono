from unittest.mock import Mock

import pytest

from omniagents.execution import AgentHostResources
from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec, SafeAgentOptions
from omniagents.core.capabilities import parse_capability_spec
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope


async def _fake_sf():
    return {"ok": True}


def _sf_registry(*names):
    return {
        n: {
            "func": _fake_sf,
            "name": n,
            "description": f"desc {n}",
            "params_schema": None,
            "result_schema": None,
            "on_invoke": None,
            "strict": True,
            "resource_scope": ResourceScope.EXTENSION,
            "execution_requirements": ExecutionRequirement.NONE,
        }
        for n in names
    }


def _make_service(caps):
    spec = Mock(spec=AgentSpec)
    spec.name = "t"
    spec.model_name = "gpt-4"
    spec.safe_agent_options = SafeAgentOptions()
    spec.use_safe_agent = False
    spec.context = None
    spec.variables = None
    spec.welcome_text = ""
    spec.realtime_mode = False
    spec.project_slug = "test_project"
    spec.agent_slug = "test_agent"
    spec.resolved_capabilities = caps
    return AgentService(
        spec,
        AgentHostResources.create(
            "test-unit-core-capabilities-capability_server_functions-host"
        ),
    )


def test_parse_resolves_server_functions():
    cap = parse_capability_spec(
        {"name": "demo", "server_functions": ["demo.run", "demo.stop"]},
        server_functions_registry=_sf_registry("demo.run", "demo.stop"),
    )
    names = [m["name"] for m in cap.server_functions()]
    assert names == ["demo.run", "demo.stop"]


def test_commands_only_capability_is_legal():
    cap = parse_capability_spec(
        {"name": "cmds", "server_functions": ["x"]},
        server_functions_registry=_sf_registry("x"),
    )
    assert cap.tools() == []
    assert len(cap.server_functions()) == 1


def test_unknown_server_function_fails_fast():
    with pytest.raises(ValueError, match="unknown server function"):
        parse_capability_spec(
            {"name": "demo", "server_functions": ["nope"]},
            server_functions_registry=_sf_registry("other"),
        )


def test_missing_registry_fails_fast():
    with pytest.raises(ValueError, match="no server_functions_registry"):
        parse_capability_spec({"name": "demo", "server_functions": ["x"]})


def test_service_registers_capability_server_functions():
    cap = parse_capability_spec(
        {"name": "demo", "server_functions": ["demo.run"]},
        server_functions_registry=_sf_registry("demo.run"),
    )
    service = _make_service([cap])
    assert "demo.run" in service._server_functions
    assert service._server_functions["demo.run"]["description"] == "desc demo.run"


def test_service_without_capability_does_not_register():
    service = _make_service([])
    assert "demo.run" not in service._server_functions


def test_duplicate_across_capabilities_first_wins():
    reg = _sf_registry("dup.fn")

    async def _other():
        return {"other": True}

    reg2 = dict(reg)
    reg2["dup.fn"] = {**reg["dup.fn"], "func": _other, "description": "second"}

    cap_a = parse_capability_spec(
        {"name": "a", "server_functions": ["dup.fn"]},
        server_functions_registry=reg,
    )
    cap_b = parse_capability_spec(
        {"name": "b", "server_functions": ["dup.fn"]},
        server_functions_registry=reg2,
    )
    service = _make_service([cap_a, cap_b])
    assert service._server_functions["dup.fn"]["description"] == "desc dup.fn"


def test_later_registration_overwrites_capability_version():
    """The project registrar runs after service construction and must win
    over a capability's registration (project overrides builtin pack)."""
    cap = parse_capability_spec(
        {"name": "demo", "server_functions": ["demo.run"]},
        server_functions_registry=_sf_registry("demo.run"),
    )
    service = _make_service([cap])

    async def _project_version():
        return {"project": True}

    service.register_server_function(
        "demo.run",
        _project_version,
        resource_scope=ResourceScope.EXTENSION,
        execution_requirements=ExecutionRequirement.NONE,
        description="project wins",
    )
    assert service._server_functions["demo.run"]["description"] == "project wins"
