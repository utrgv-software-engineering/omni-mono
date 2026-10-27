"""Studio's agents API: what each project agent runs with, and editing it.

Reading goes to the agent's runtime (:class:`AgentRuntimes`), so the Agents
screen shows what a run is given. Editing goes through
:mod:`omniagents.core.project.agent_files`, the one writer for agent files:
only the edited settings change, and nothing that would not load is
written. After a change the project's agents are reloaded, so the next
Playground connection runs the saved agent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from omniagents.core.project.agent_files import (
    AgentEditError,
    create_agent,
    delete_agent,
    read_agent,
    update_agent,
    write_agent_yaml,
)

from .agent_runtimes import AgentRuntimes

router = APIRouter(prefix="/api/agents")


def _runtimes(request: Request) -> AgentRuntimes:
    return request.app.state.agent_runtimes


def _manifest(request: Request) -> Path:
    resources = getattr(request.app.state, "project_resources", {}) or {}
    hint = resources.get("manifest_path")
    if hint and Path(hint).expanduser().is_file():
        return Path(hint).expanduser().resolve()
    raise HTTPException(
        status_code=404, detail="Launch Studio from a project to edit its agents"
    )


def _edit(request: Request, action):
    """Run an edit; a refused one answers 422 with each problem."""
    try:
        result = action()
    except AgentEditError as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _runtimes(request).reload()
    return result


@router.get("")
def list_agents(request: Request) -> dict[str, Any]:
    return {"agents": _runtimes(request).agents()}


@router.get("/options")
def agent_options(request: Request) -> dict[str, Any]:
    """What an agent can name: the project's capabilities, tools,
    guardrails and other agents."""
    from omniagents.core.project.runtime import load_project

    project = load_project(str(_manifest(request)))
    capabilities = []
    for name, capability in sorted(project.capability_registry.items()):
        try:
            tools = [getattr(t, "name", None) or str(t) for t in capability.tools()]
        except Exception:  # a capability whose tools do not resolve
            tools = []
        capabilities.append(
            {
                "name": name,
                "description": (
                    getattr(capability, "instructions_text", None) or ""
                ).strip(),
                "tools": sorted(tools),
            }
        )
    return {
        "capabilities": capabilities,
        "tools": sorted(project.tool_registry),
        "guardrails": {
            kind: sorted(registry)
            for kind, registry in (project.guardrails_registry or {}).items()
        },
        "agents": sorted(project.agents_map),
    }


@router.get("/{agent_key}")
async def describe_agent(request: Request, agent_key: str) -> dict[str, Any]:
    """What a run of the agent is given, from its runtime: the built
    agent's model, tools and rendered instructions, its subagents,
    guardrails, structured output, capabilities, skills and approvals."""
    runtimes = _runtimes(request)
    error = runtimes.load_error(agent_key)
    if error is not None:
        raise HTTPException(
            status_code=422, detail=f"Agent '{agent_key}' does not load: {error}"
        )
    try:
        described = await runtimes.describe(agent_key)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not build agent '{agent_key}': {exc}"
        ) from exc
    if described is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_key}' not found")
    return described


@router.get("/{agent_key}/source")
def agent_source(request: Request, agent_key: str) -> dict[str, Any]:
    """The agent's ``agent.yml`` and instructions, as on disk."""
    try:
        return read_agent(_manifest(request), agent_key).to_dict()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class AgentChanges(BaseModel):
    #: setting -> new value; ``null`` removes the setting.
    changes: dict[str, Any] = {}
    instructions: str | None = None


@router.patch("/{agent_key}")
def edit_agent(request: Request, agent_key: str, body: AgentChanges) -> dict[str, Any]:
    return _edit(
        request,
        lambda: update_agent(
            _manifest(request),
            agent_key,
            changes=body.changes,
            instructions=body.instructions,
        ).to_dict(),
    )


class AgentYaml(BaseModel):
    yaml: str


@router.put("/{agent_key}/source")
def replace_agent_yaml(
    request: Request, agent_key: str, body: AgentYaml
) -> dict[str, Any]:
    return _edit(
        request,
        lambda: write_agent_yaml(_manifest(request), agent_key, body.yaml).to_dict(),
    )


class NewAgent(BaseModel):
    key: str
    model: str
    label: str | None = None
    description: str | None = None


@router.post("")
def add_agent(request: Request, body: NewAgent) -> dict[str, Any]:
    return _edit(
        request,
        lambda: create_agent(
            _manifest(request),
            body.key,
            model=body.model,
            label=body.label,
            description=body.description,
        ).to_dict(),
    )


@router.delete("/{agent_key}")
def remove_agent(request: Request, agent_key: str) -> dict[str, Any]:
    entrypoint = _edit(request, lambda: delete_agent(_manifest(request), agent_key))
    return {"removed": agent_key, "entrypoint": entrypoint}
