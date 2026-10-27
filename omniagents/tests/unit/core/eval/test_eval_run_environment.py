"""An evaluated agent's tools run in the scenario's environment.

A run is routed to an execution environment registered in its AgentHost;
an evaluation that registered none ran every agent targetless, so each
file and shell tool failed with "No workspace is bound to this run".
These tests make a real tool call through the evaluation target.
"""

import asyncio
import json
import textwrap
from pathlib import Path
from types import SimpleNamespace

from agents import Model
from agents.items import ModelResponse
from agents.usage import Usage
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseUsage,
)
from openai.types.responses.response_usage import (
    InputTokensDetails,
    OutputTokensDetails,
)

from omniagents.core.eval.local_workspace import LocalWorkspaceEnvironment
from omniagents.core.eval.targets import ProjectAgentTarget
from omniagents.execution import EnvironmentCapability, EnvironmentKind


class _ReadsMarker(Model):
    """Reads ``marker.txt`` with the agent's file tool, then repeats its output."""

    def _output(self, input):
        items = input if isinstance(input, list) else []
        result = next(
            (i for i in items if i.get("type") == "function_call_output"), None
        )
        if result is None:
            return [
                ResponseFunctionToolCall(
                    id="fc_1",
                    type="function_call",
                    name="read_file",
                    call_id="call_1",
                    arguments=json.dumps({"file_path": "marker.txt"}),
                )
            ]
        output = result.get("output")
        text = output if isinstance(output, str) else json.dumps(output)
        return [
            ResponseOutputMessage(
                id="msg_1",
                type="message",
                role="assistant",
                status="completed",
                content=[
                    ResponseOutputText(type="output_text", text=text, annotations=[])
                ],
            )
        ]

    async def get_response(self, system_instructions, input, *args, **kwargs):
        return ModelResponse(
            output=self._output(input),
            usage=Usage(requests=1, input_tokens=1, output_tokens=1, total_tokens=2),
            response_id="resp",
        )

    async def stream_response(self, system_instructions, input, *args, **kwargs):
        yield ResponseCompletedEvent(
            type="response.completed",
            sequence_number=0,
            response=Response(
                id="resp",
                created_at=0,
                object="response",
                model="fake",
                parallel_tool_calls=False,
                tool_choice="auto",
                tools=[],
                output=self._output(input),
                usage=ResponseUsage(
                    input_tokens=1,
                    output_tokens=1,
                    total_tokens=2,
                    input_tokens_details=InputTokensDetails(cached_tokens=0),
                    output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
                ),
            ),
        )


def _project(root: Path) -> Path:
    (root / "agents/reader").mkdir(parents=True)
    (root / "project.yml").write_text("agents: {entrypoint: reader}\n")
    (root / "agents/reader/agent.yml").write_text(textwrap.dedent("""
            name: reader
            model: fake-model
            instructions: Read files.
            capabilities: [filesystem]
            use_safe_agent: false   # routing, not approvals, is under test
            """).lstrip())
    fixture = root / "fixtures/repo"
    fixture.mkdir(parents=True)
    (fixture / "marker.txt").write_text("the scenario workspace\n")
    return root


def _install_model(monkeypatch):
    from agents.run_internal import run_loop

    model = _ReadsMarker()
    monkeypatch.setattr(run_loop, "get_model", lambda agent, run_config: model)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-offline-test")


def test_file_tools_read_the_scenario_workspace(tmp_path, monkeypatch):
    _install_model(monkeypatch)
    root = _project(tmp_path / "project")
    monkeypatch.chdir(tmp_path)  # not the workspace: the run must be routed there
    env = LocalWorkspaceEnvironment(
        fixtures_dir=root / "fixtures", workspace_cache_dir=tmp_path / "cache"
    )
    target = ProjectAgentTarget(root / "agents/reader/agent.yml", project_root=root)

    async def scenario():
        context = await env.setup({"name": "s", "fixture": "repo"})
        run = await target.start(
            {"name": "s"}, defaults={}, environment=env, context=context
        )
        try:
            await run.run_turn("Read the marker.")
            return run.history(), run.run_error()
        finally:
            await run.cleanup()
            await env.teardown(context)

    history, error = asyncio.run(scenario())
    assert error is None
    outputs = [
        json.dumps(item.get("output"))
        for item in history
        if item.get("type") == "function_call_output"
    ]
    assert outputs and "the scenario workspace" in outputs[0]
    assert "No workspace is bound" not in outputs[0]


def _resources():
    from omniagents.execution import AgentHostResources

    return AgentHostResources.create("eval-test")


def test_default_environment_is_the_staged_workspace(tmp_path):
    from omniagents.core.eval.environment import EnvironmentContext, NoOpEnvironment

    resources = _resources()
    context = EnvironmentContext("s", {"workspace_root": str(tmp_path)})
    descriptor = asyncio.run(NoOpEnvironment().register_environment(resources, context))
    lease = asyncio.run(resources.environments.acquire(descriptor.environment_id))
    assert descriptor.kind is EnvironmentKind.HOST
    assert lease.workspace.root == str(tmp_path)


def test_container_scenarios_register_their_sandbox(tmp_path, monkeypatch):
    import omniagents.core.sandbox.factory as factory
    from omniagents.core.eval.container_routing import ContainerRoutingMixin
    from omniagents.workspace.sandbox import SandboxWorkspace

    session = SimpleNamespace(
        state=SimpleNamespace(container_id="c0ffee"),
        manifest=SimpleNamespace(root="/workspace"),
    )

    async def _noop(*args, **kwargs):
        return SimpleNamespace(exit_code=0, stdout=b"", stderr=b"")

    session.start = session.stop = session.aclose = _noop
    session.exec = _noop

    class _Client:
        async def create(self, *, manifest, options, snapshot):
            return session

    monkeypatch.setattr(factory, "build_sandbox_client", lambda raw: (_Client(), {}))

    class Env(ContainerRoutingMixin, LocalWorkspaceEnvironment):
        profile_resolver = staticmethod(
            lambda *, workspace_dir, explicit_path: SimpleNamespace(
                raw={"client": {"type": "docker"}, "run_as": "dev"}, build=[], init=[]
            )
        )

    env = Env(workspace_cache_dir=tmp_path / "cache")
    profile = tmp_path / "p.yml"
    profile.write_text("client: {type: docker}\n")
    resources = _resources()

    async def scenario():
        context = await env.setup({"environment": {"profile": str(profile)}})
        try:
            descriptor = await env.register_environment(resources, context)
            return descriptor, await resources.environments.acquire(
                descriptor.environment_id
            )
        finally:
            await env.teardown(context)

    descriptor, lease = asyncio.run(scenario())
    assert descriptor.kind is EnvironmentKind.CONTAINER
    # As production marks a docker sandbox: the approval policy's sandbox
    # tiers key on it.
    assert EnvironmentCapability.CONTAINED in descriptor.capabilities
    assert isinstance(lease.workspace, SandboxWorkspace)
    assert lease.workspace.root == "/workspace"
    assert lease.sandbox_runtime.session is session
    assert lease.sandbox_runtime.run_as == "dev"
