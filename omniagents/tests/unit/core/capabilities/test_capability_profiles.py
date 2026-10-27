from types import SimpleNamespace

import pytest

from omniagents.core.capabilities.profiles import spec_for_capability_profile


def cap(name, *tools):
    return SimpleNamespace(
        capability_name=name,
        tools=lambda: [SimpleNamespace(name=t) for t in tools],
        server_function_specs=[],
        agent_tool_configs=[],
    )


def fixture():
    workers, swarms = cap("workers", "spawn_worker"), cap("swarms", "create_swarm")
    return SimpleNamespace(
        resolved_capabilities=[workers],
        capability_profiles={"resident": {"workers": "swarms"}},
        _capability_registry={"workers": workers, "swarms": swarms},
    )


def test_resident_selection_is_local_and_regular_chat_keeps_workers():
    spec = fixture()
    resident = SimpleNamespace(variables={"capability_profile": "resident"})
    selected = spec_for_capability_profile(spec, resident)
    assert selected is not spec
    assert selected.resolved_capabilities[0].capability_name == "swarms"
    assert (
        spec_for_capability_profile(spec, SimpleNamespace(variables={}))
        .resolved_capabilities[0]
        .capability_name
        == "workers"
    )
    assert spec.resolved_capabilities[0].capability_name == "workers"
    assert (
        spec_for_capability_profile(spec, resident).resolved_capabilities
        == selected.resolved_capabilities
    )


def test_unknown_profile_fails_instead_of_silently_using_workers():
    with pytest.raises(ValueError, match="Unknown capability profile"):
        spec_for_capability_profile(
            fixture(), SimpleNamespace(variables={"capability_profile": "typo"})
        )


def test_profiles_cannot_introduce_unregistered_control_plane():
    spec = fixture()
    spec._capability_registry["swarms"].server_function_specs = [{"name": "new"}]
    with pytest.raises(ValueError, match="preserve server functions"):
        spec_for_capability_profile(
            spec, SimpleNamespace(variables={"capability_profile": "resident"})
        )


@pytest.mark.asyncio
async def test_actual_builder_uses_profile_tools_and_instructions():
    from agents import function_tool
    from omniagents.core.agents.builder import _default_build_agent
    from omniagents.core.agents.specs import AgentSpec

    @function_tool
    def worker_tool() -> str:
        """Existing worker path."""
        return "worker"

    @function_tool
    def swarm_tool() -> str:
        """Resident swarm path."""
        return "swarm"

    workers, swarms = cap("workers"), cap("swarms")
    workers.tools = lambda: [worker_tool]
    swarms.tools = lambda: [swarm_tool]
    workers.instructions_text = "Use existing workers."
    swarms.instructions_text = "Use resident swarms."
    spec = AgentSpec(
        name="test",
        welcome_text="",
        get_agent_instructions=lambda: "Work carefully.",
        build_agent=None,
        resolved_capabilities=[workers],
        capability_profiles={"resident": {"workers": "swarms"}},
    )
    spec._capability_registry = {"workers": workers, "swarms": swarms}
    session = SimpleNamespace(variables={"capability_profile": "resident"})
    resident = await _default_build_agent({}, None, spec, session=session)
    regular = await _default_build_agent({}, None, spec)
    # Every built agent also gets the output-budget reader.
    assert [t.name for t in resident.tools] == ["swarm_tool", "read_tool_output"]
    assert [t.name for t in regular.tools] == ["worker_tool", "read_tool_output"]
    rendered = await resident.instructions(SimpleNamespace(context={}), resident)
    assert "Use resident swarms." in rendered
    assert "Use existing workers." not in rendered
    utility = AgentSpec(
        name="utility",
        welcome_text="",
        get_agent_instructions=lambda: "Inspect only.",
        build_agent=None,
    )
    assert spec_for_capability_profile(utility, session) is utility
