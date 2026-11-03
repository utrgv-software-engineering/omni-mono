"""``agent_tools`` entries with ``mode: conversation``: a tool handing work over."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from omniagents.core.agents.builder import _build_agent_tools_from_configs
from omniagents.core.agents.handoffs import set_handler
from omniagents.core.agents.specs import AgentToolConfig


def _config(tmp_path):
    target = tmp_path / "agents" / "designer"
    target.mkdir(parents=True)
    (target / "agent.yml").write_text(
        "name: designer\nlabel: Designer\ndescription: Designs.\n"
    )
    return AgentToolConfig(
        yaml_path=str(target / "agent.yml"), tool_name="design", mode="conversation"
    )


def test_mode_is_call_or_conversation():
    with pytest.raises(ValueError):
        AgentToolConfig(yaml_path="x", mode="sideways")


def test_the_tool_hands_the_request_to_the_handler(tmp_path):
    asked = []

    async def hand_off(session_id, caller, agent, request):
        asked.append((session_id, caller, agent, request))
        return {"handed_to": agent, "status": "started"}

    (tool,) = asyncio.run(
        _build_agent_tools_from_configs(
            [_config(tmp_path)], {}, parent_spec=SimpleNamespace(name="foreman")
        )
    )
    assert tool.name == "design" and "Designer" in tool.description
    ctx = SimpleNamespace(context={"session_id": "s-1"})
    assert json.loads(
        asyncio.run(tool.on_invoke_tool(ctx, json.dumps({"request": "x"})))
    )["error"].startswith("Handing work over needs a factory")
    set_handler(hand_off)
    result = asyncio.run(tool.on_invoke_tool(ctx, json.dumps({"request": "Draw it."})))
    assert json.loads(result) == {"handed_to": "designer", "status": "started"}
    assert asked == [("s-1", "foreman", "designer", "Draw it.")]


def test_the_loader_reads_the_mode(tmp_path):
    from omniagents.core.project.runtime import build_spec_from_project, load_project

    (tmp_path / "project.yml").write_text("agents: {entrypoint: front}\n")
    for name, extra in [
        ("front", "agent_tools:\n  - agent: designer\n    mode: conversation\n"),
        ("designer", ""),
    ]:
        (tmp_path / "agents" / name).mkdir(parents=True)
        (tmp_path / "agents" / name / "agent.yml").write_text(
            f"name: {name}\ninstructions: Work.\n{extra}"
        )
    spec = build_spec_from_project(load_project(str(tmp_path / "project.yml")), "front")
    (config,) = [
        c for c in spec.agent_tool_configs if c.yaml_path.endswith("designer/agent.yml")
    ]
    assert config.mode == "conversation"
