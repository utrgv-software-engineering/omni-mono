"""What a built agent works with, as plain data.

``AgentService.describe_agent`` builds an agent exactly as a run does and
hands it here: the result is what operator tools (Studio's Agents screen)
show, so what they display is what runs rather than a re-reading of the
YAML.
"""

from __future__ import annotations

import inspect
from typing import Any


def model_name(agent: Any) -> str | None:
    """The model a run of *agent* calls: a name, or a provider model object
    that carries one."""
    model = getattr(agent, "model", None)
    if model is None:
        return None
    if isinstance(model, str):
        return model
    name = getattr(model, "model", None) or getattr(model, "model_name", None)
    return str(name) if name else type(model).__name__


def model_settings(agent: Any) -> dict[str, Any]:
    settings = getattr(agent, "model_settings", None)
    to_dict = getattr(settings, "to_json_dict", None)
    if callable(to_dict):
        return {key: value for key, value in to_dict().items() if value is not None}
    return {}


def _approval(tool: Any) -> str:
    """``always`` asks first, ``conditional`` decides per call (a safe-agent
    predicate, an allow list), ``never`` runs without asking."""
    needs = getattr(tool, "needs_approval", False)
    if callable(needs):
        return "conditional"
    return "always" if needs else "never"


def tools(agent: Any, agent_tool_names: set[str]) -> list[dict[str, Any]]:
    described = []
    for tool in getattr(agent, "tools", None) or []:
        name = getattr(tool, "name", None) or type(tool).__name__
        described.append(
            {
                "name": name,
                "description": getattr(tool, "description", "") or "",
                "kind": "agent" if name in agent_tool_names else _tool_kind(tool),
                "approval": _approval(tool),
                "parameters": getattr(tool, "params_json_schema", None),
            }
        )
    return sorted(described, key=lambda tool: tool["name"].lower())


def _tool_kind(tool: Any) -> str:
    kind = type(tool).__name__
    return "function" if kind == "FunctionTool" else kind


def handoffs(agent: Any) -> list[dict[str, Any]]:
    described = []
    for handoff in getattr(agent, "handoffs", None) or []:
        name = getattr(handoff, "agent_name", None) or getattr(handoff, "name", None)
        described.append(
            {
                "name": str(name or type(handoff).__name__),
                "tool": getattr(handoff, "tool_name", None),
                "description": getattr(handoff, "tool_description", None)
                or getattr(handoff, "handoff_description", None)
                or "",
            }
        )
    return described


async def instructions(agent: Any, context: Any) -> dict[str, Any]:
    """The system prompt a run starts with. Dynamic instructions are
    rendered against the run context; one that cannot be rendered outside a
    run reports why instead of guessing."""
    value = getattr(agent, "instructions", None)
    if value is None or isinstance(value, str):
        return {"text": value or "", "dynamic": False, "error": None}
    try:
        from agents.run_context import RunContextWrapper

        rendered = value(RunContextWrapper(context=context), agent)
        if inspect.isawaitable(rendered):
            rendered = await rendered
        return {"text": str(rendered or ""), "dynamic": True, "error": None}
    except Exception as exc:
        return {"text": "", "dynamic": True, "error": f"{type(exc).__name__}: {exc}"}


def _guardrail_name(guardrail: Any) -> str:
    get_name = getattr(guardrail, "get_name", None)
    if callable(get_name):
        return str(get_name())
    return str(getattr(guardrail, "name", None) or type(guardrail).__name__)


def guardrails(agent: Any) -> dict[str, list[str]]:
    """The checks a run applies: to the user's input and the final output
    (the agent's own), and to every tool call's arguments and result (set
    on each tool)."""
    tool_input: list[str] = []
    tool_output: list[str] = []
    for tool in getattr(agent, "tools", None) or []:
        for name in map(
            _guardrail_name, getattr(tool, "tool_input_guardrails", None) or []
        ):
            if name not in tool_input:
                tool_input.append(name)
        for name in map(
            _guardrail_name, getattr(tool, "tool_output_guardrails", None) or []
        ):
            if name not in tool_output:
                tool_output.append(name)
    return {
        "input": [
            _guardrail_name(g) for g in getattr(agent, "input_guardrails", None) or []
        ],
        "output": [
            _guardrail_name(g) for g in getattr(agent, "output_guardrails", None) or []
        ],
        "tool_input": tool_input,
        "tool_output": tool_output,
    }


def output(agent: Any) -> dict[str, Any] | None:
    """The structured output a run must end with, or ``None`` for text."""
    output_type = getattr(agent, "output_type", None)
    if output_type is None or output_type is str:
        return None
    from agents.agent_output import AgentOutputSchema, AgentOutputSchemaBase

    schema = (
        output_type
        if isinstance(output_type, AgentOutputSchemaBase)
        else AgentOutputSchema(output_type)
    )
    if schema.is_plain_text():
        return None
    return {
        "name": schema.name(),
        "strict": schema.is_strict_json_schema(),
        "schema": schema.json_schema(),
    }


def subagent_name(config: Any) -> str:
    inline = getattr(config, "inline_spec", None)
    if inline is not None and getattr(inline, "name", None):
        return str(inline.name)
    path = getattr(config, "yaml_path", None)
    if path:
        from pathlib import Path

        candidate = Path(path)
        return (
            candidate.parent.name if candidate.name == "agent.yml" else candidate.stem
        )
    return "agent"
