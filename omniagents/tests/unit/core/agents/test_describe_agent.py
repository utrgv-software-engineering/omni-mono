import textwrap
from pathlib import Path

import pytest

from omniagents.core.agents.service import AgentService
from omniagents.core.project.runtime import build_spec_from_project, load_project
from omniagents.execution import AgentHostResources


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip())


def _service(root: Path, agent_yml: str) -> AgentService:
    _write(root / "project.yml", "name: describe-test\n")
    _write(root / "agents" / "main" / "agent.yml", agent_yml)
    spec = build_spec_from_project(load_project(str(root)))
    return AgentService(spec, AgentHostResources.create("describe-test"))


@pytest.mark.asyncio
async def test_describes_what_a_run_is_given(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    service = _service(
        tmp_path,
        """
        name: main
        model: gpt-5-mini
        instructions: "You work in {{ workspace_root }}."
        capabilities: [filesystem]
        """,
    )

    described = await service.describe_agent(workspace_root="/work/here")

    assert described["name"] == "main"
    assert described["model"]["name"] == "gpt-5-mini"
    # Capability tools are what the run's agent carries, not a YAML list.
    names = {tool["name"] for tool in described["tools"]}
    assert {"read_file", "write_file"} <= names
    assert described["capabilities"] == ["filesystem"]
    # Instructions are rendered against the run's context.
    assert "/work/here" in described["instructions"]["text"]
    assert described["instructions"]["error"] is None
    assert "{{" not in described["instructions"]["text"]


@pytest.mark.asyncio
async def test_describing_starts_no_mcp_server_and_stores_nothing(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    service = _service(
        tmp_path,
        """
        name: main
        model: gpt-5-mini
        instructions: hi
        mcp_servers:
          - name: files
            type: stdio
            params: { command: definitely-not-a-real-binary }
        """,
    )

    async def refuse(*args, **kwargs):
        raise AssertionError("describing must not start MCP servers")

    monkeypatch.setattr(service, "_ensure_session_mcp_servers", refuse)
    described = await service.describe_agent()

    assert described["mcp_servers"] == [{"name": "files", "type": "stdio"}]
    assert service._session_manager.list_sessions() == []


@pytest.mark.asyncio
async def test_describes_guardrails_and_structured_output(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _write(
        tmp_path / "guardrails" / "no_secrets.py",
        """
        from omniagents import input_guardrail, GuardrailFunctionOutput

        @input_guardrail()
        def no_secrets(context, agent, input):
            return GuardrailFunctionOutput(output_info={}, tripwire_triggered=False)
        """,
    )
    service = _service(
        tmp_path,
        """
        name: main
        model: gpt-5-mini
        instructions: hi
        input_guardrails: [no_secrets]
        output_schema:
          properties:
            summary:
              type: string
        """,
    )

    described = await service.describe_agent()

    assert described["guardrails"] == {
        "input": ["no_secrets"],
        "output": [],
        "tool_input": [],
        "tool_output": [],
    }
    assert described["output"]["strict"] is True
    assert described["output"]["schema"]["properties"]["summary"]["type"] == "string"


@pytest.mark.asyncio
async def test_a_text_agent_has_no_structured_output(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    service = _service(tmp_path, "name: main\nmodel: gpt-5-mini\ninstructions: hi\n")

    assert (await service.describe_agent())["output"] is None
