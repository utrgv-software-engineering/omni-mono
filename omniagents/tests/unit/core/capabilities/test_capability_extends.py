import pytest

from omniagents.core.capabilities import parse_capability_spec


def _tool(name):
    class _T:
        pass

    t = _T()
    t.name = name
    return t


def _tool_registry(*names):
    return {n: _tool(n) for n in names}


async def _fake_sf():
    return {"ok": True}


def _sf_registry(*names):
    return {
        n: {"func": _fake_sf, "name": n, "description": None, "params_schema": None,
            "result_schema": None, "on_invoke": None, "strict": True}
        for n in names
    }


@pytest.fixture()
def base_registry():
    tools = _tool_registry("read", "grep", "walk", "extra")
    sfs = _sf_registry("pack.list", "pack.kill", "pack.other")
    base = parse_capability_spec(
        {
            "name": "pack",
            "tools": ["read", "grep", "walk"],
            "server_functions": ["pack.list", "pack.kill"],
            "instructions": "base instructions",
            "approvals": {"read": {"mode": "never"}},
            "sampling_params": {"temperature": 0.1},
        },
        tool_registry=tools,
        server_functions_registry=sfs,
    )
    return {"pack": base}, tools, sfs


def test_extends_inherits_everything(base_registry):
    registry, tools, sfs = base_registry
    child = parse_capability_spec(
        {"name": "pack", "extends": "pack", "instructions": "custom"},
        tool_registry=tools,
        server_functions_registry=sfs,
        capability_registry=registry,
    )
    assert [t.name for t in child.tools()] == ["read", "grep", "walk"]
    assert [m["name"] for m in child.server_functions()] == ["pack.list", "pack.kill"]
    assert child.instructions_text == "custom"
    assert child.approvals_spec == {"read": {"mode": "never"}}


def test_exclude_tools_and_append(base_registry):
    registry, tools, sfs = base_registry
    child = parse_capability_spec(
        {
            "name": "pack",
            "extends": "pack",
            "exclude_tools": ["grep", "walk"],
            "tools": ["extra"],
        },
        tool_registry=tools,
        server_functions_registry=sfs,
        capability_registry=registry,
    )
    assert [t.name for t in child.tools()] == ["read", "extra"]
    # instructions inherited when child omits them
    assert child.instructions_text == "base instructions"


def test_exclude_server_functions(base_registry):
    registry, tools, sfs = base_registry
    child = parse_capability_spec(
        {"name": "pack", "extends": "pack", "exclude_server_functions": ["pack.kill"]},
        tool_registry=tools,
        server_functions_registry=sfs,
        capability_registry=registry,
    )
    assert [m["name"] for m in child.server_functions()] == ["pack.list"]


def test_exclude_unknown_tool_fails_fast(base_registry):
    registry, tools, sfs = base_registry
    with pytest.raises(ValueError, match="exclude_tools references tools not in"):
        parse_capability_spec(
            {"name": "pack", "extends": "pack", "exclude_tools": ["nope"]},
            tool_registry=tools,
            server_functions_registry=sfs,
            capability_registry=registry,
        )


def test_extends_unknown_base_fails_fast(base_registry):
    registry, tools, sfs = base_registry
    with pytest.raises(ValueError, match="extends unknown capability"):
        parse_capability_spec(
            {"name": "x", "extends": "missing"},
            tool_registry=tools,
            capability_registry=registry,
        )


def test_exclude_without_extends_fails_fast(base_registry):
    _, tools, _ = base_registry
    with pytest.raises(ValueError, match="without 'extends:'"):
        parse_capability_spec(
            {"name": "x", "tools": ["read"], "exclude_tools": ["read"]},
            tool_registry=tools,
        )


def test_extends_with_type_fails_fast(base_registry):
    registry, tools, _ = base_registry
    with pytest.raises(ValueError, match="cannot combine 'extends' with 'type:'"):
        parse_capability_spec(
            {"name": "x", "extends": "pack", "type": "sdk_filesystem"},
            tool_registry=tools,
            capability_registry=registry,
        )


def test_sampling_and_approvals_merge_child_over_base(base_registry):
    registry, tools, sfs = base_registry
    child = parse_capability_spec(
        {
            "name": "pack",
            "extends": "pack",
            "approvals": {"grep": {"mode": "always"}},
            "sampling_params": {"temperature": 0.7},
        },
        tool_registry=tools,
        server_functions_registry=sfs,
        capability_registry=registry,
    )
    assert child.approvals_spec == {
        "read": {"mode": "never"},
        "grep": {"mode": "always"},
    }
    assert child.sampling_params_override == {"temperature": 0.7}


def test_instructions_append_composes_base_text(base_registry):
    registry, tools, sfs = base_registry
    child = parse_capability_spec(
        {"name": "pack", "extends": "pack", "instructions_append": "extra line\n"},
        tool_registry=tools,
        server_functions_registry=sfs,
        capability_registry=registry,
    )
    assert child.instructions_text == "base instructions\nextra line\n"
    # Surface still fully inherited.
    assert [t.name for t in child.tools()] == ["read", "grep", "walk"]


def test_instructions_append_without_extends_fails_fast():
    tools = _tool_registry("read")
    with pytest.raises(ValueError, match="instructions_append"):
        parse_capability_spec(
            {"name": "solo", "tools": ["read"], "instructions_append": "x"},
            tool_registry=tools,
        )


def test_instructions_append_with_instructions_fails_fast(base_registry):
    registry, tools, sfs = base_registry
    with pytest.raises(ValueError, match="cannot combine"):
        parse_capability_spec(
            {
                "name": "pack",
                "extends": "pack",
                "instructions": "replace",
                "instructions_append": "and append",
            },
            tool_registry=tools,
            server_functions_registry=sfs,
            capability_registry=registry,
        )
