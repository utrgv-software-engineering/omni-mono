"""Reusable pytest fixtures for testing OmniAgents tools.

For test suites — the framework's own and downstream products' — that
invoke builtin-style tools directly, outside the AgentService run path.
Import the fixtures into a ``conftest.py`` so pytest collects them with
that conftest's directory scoping (deliberately NOT a ``pytest11``
entry-point plugin: the permissive-workspace fixture is autouse, and
plugin registration would apply it session-wide instead of only where a
conftest opts in)::

    from omniagents.testing import (  # noqa: F401
        _default_permissive_workspace,
        invoke_tool,
        test_event_loop,
    )

Requires ``pytest`` — import this module from test code only.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, AsyncIterator, List, Optional

import pytest
from agents import Model
from agents.items import ModelResponse
from agents.tool import ToolContext
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

from omniagents.execution import (
    EnvironmentCapability,
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
from omniagents.workspace import HostWorkspace, reset_workspace, set_workspace

_TEST_ENVIRONMENT_ID = "test-host-environment"
_TEST_WORKSPACE_ID = "test-workspace"
_TEST_ENVIRONMENT_GENERATION = 1
_TEST_SESSION_ID = "sess-test"


def _direct_invocation_context(workspace: HostWorkspace) -> RequestContext:
    """Build the dispatcher context used by direct test invocations."""
    descriptor = ExecutionEnvironmentDescriptor(
        environment_id=_TEST_ENVIRONMENT_ID,
        workspace_id=_TEST_WORKSPACE_ID,
        kind=EnvironmentKind.HOST,
        state=EnvironmentState.READY,
        generation=_TEST_ENVIRONMENT_GENERATION,
        capabilities=frozenset(EnvironmentCapability),
    )
    return RequestContext(
        operation=OperationExecutionDescriptor(
            name="testing.direct_invocation",
            resource_scope=ResourceScope.ENVIRONMENT,
            execution_requirements=(
                ExecutionRequirement.WORKSPACE_READ
                | ExecutionRequirement.WORKSPACE_WRITE
                | ExecutionRequirement.PROCESS
                | ExecutionRequirement.NETWORK
                | ExecutionRequirement.PTY
                | ExecutionRequirement.ENVIRONMENT_LIFECYCLE
            ),
        ),
        principal=None,
        workspace=WorkspaceResource(_TEST_WORKSPACE_ID),
        environment=descriptor,
        environment_lease=EnvironmentLease(descriptor, workspace),
    )


@pytest.fixture(autouse=True)
def _default_permissive_workspace():
    """Install an explicit permissive host environment for every test.

    Direct tool/server-function tests bypass dispatcher routing. Model that
    boundary with a real ``RequestContext`` and ``EnvironmentLease`` instead
    of reviving an implicit host fallback. ``invoke_tool`` may still swap in a
    tighter Workspace adapter for one tool invocation.
    """
    workspace = HostWorkspace(root="/")
    workspace_token = set_workspace(workspace)
    request_token = bind_request_context(_direct_invocation_context(workspace))
    try:
        yield
    finally:
        reset_request_context(request_token)
        reset_workspace(workspace_token)


@pytest.fixture
def test_event_loop():
    """One asyncio event loop shared across every ``invoke_tool`` call in
    a single test.

    Background bash jobs spawn asyncio subprocesses whose I/O machinery
    (drain task, child watcher, watcher task in ``_bash_jobs._watch_job``)
    is bound to whichever loop they were created in. If each
    ``invoke_tool`` call ran its own loop, the asyncio subprocess from
    the first call would be orphaned by the second call's loop and the
    Job's exit code would never get cached. Sharing the loop fixes both:
    long-running spawns survive across multiple invocations, and the
    watcher task keeps observing them.
    """
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        # Cancel any tasks the test left running so close() doesn't warn.
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


@pytest.fixture
def invoke_tool(test_event_loop):
    """Invoke a tool on the shared test event loop.

    Install a matching ``HostWorkspace`` and explicit ``EnvironmentLease`` for
    the duration of each call so workspace-routed tool bodies resolve paths
    against the agent's configured root. The ToolContext also receives the
    environment identity fields that the production dispatcher supplies.

    Also exposes helper methods:
    - ``invoke_tool.run(coro)``: schedule a coroutine on the shared loop.
    - ``invoke_tool.wait_for_job_exit(job, timeout)``: block (synchronously
      from the test's POV) until the Job's watcher records an exit.
    """

    def _invoke(tool, *, workspace_root=None, **params):
        payload = {k: v for k, v in params.items() if v is not None}
        context_data = {
            "environment_id": _TEST_ENVIRONMENT_ID,
            "environment_generation": _TEST_ENVIRONMENT_GENERATION,
            "session_id": _TEST_SESSION_ID,
        }
        if workspace_root is not None:
            context_data["workspace_root"] = workspace_root
        ctx = ToolContext(
            context=context_data,
            usage=Usage(),
            tool_name=tool.name,
            tool_call_id="test-call",
            tool_arguments=payload,
        )
        workspace = (
            HostWorkspace(root=workspace_root)
            if workspace_root
            else HostWorkspace(root="/")
        )
        workspace_token = set_workspace(workspace)
        request_token = bind_request_context(_direct_invocation_context(workspace))
        try:
            return test_event_loop.run_until_complete(
                tool.on_invoke_tool(ctx, json.dumps(payload))
            )
        finally:
            reset_request_context(request_token)
            reset_workspace(workspace_token)

    async def _wait_for_exit(job, timeout):
        deadline = test_event_loop.time() + timeout
        while job.exit_code is None:
            if test_event_loop.time() > deadline:
                raise TimeoutError(f"job {job.job_id} did not exit within {timeout}s")
            await asyncio.sleep(0.05)

    def wait_for_job_exit(job, timeout: float = 5.0):
        """Block until ``job.exit_code`` is populated by the watcher.

        Replacement for the old ``job.popen.wait(timeout=...)`` pattern,
        which no longer applies now that Job wraps a ``ProcessHandle``."""
        return test_event_loop.run_until_complete(_wait_for_exit(job, timeout))

    def run(coro):
        """Run an arbitrary coroutine on the test's shared event loop."""
        return test_event_loop.run_until_complete(coro)

    _invoke.wait_for_job_exit = wait_for_job_exit  # type: ignore[attr-defined]
    _invoke.run = run  # type: ignore[attr-defined]
    return _invoke


# ---------------------------------------------------------------------------
# Offline model for evaluation tests
# ---------------------------------------------------------------------------
#
# ``FakeModel`` answers from a script keyed on the latest user message, so
# the same conversation produces the same tool calls and text whichever
# runtime drives it (the SDK ``Runner`` directly, or a project agent through
# ``LocalAgent``/``AgentService``). Rules, on the latest user message
# (lower-cased):
#
# - a judge (instructions mention "evaluation judge") answers the JSON
#   ``{"answer": "Pass"|"Fail", "reasoning", "score"}``: ``Fail`` when the
#   text under review contains "wrong";
# - "weather in <city>" calls ``get_weather(city=<city>)``, then answers
#   "It is sunny in <city>." once the tool output arrives;
# - "say nothing" gives an empty answer (a non-response);
# - "be wrong" answers "This is wrong.";
# - anything else is echoed: "You said: <message>".


def _text_of(item: Any) -> str:
    content = item.get("content") if isinstance(item, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and isinstance(c.get("text"), str):
                parts.append(c["text"])
        return "".join(parts)
    return ""


def _items(input: Any) -> List[dict]:
    if isinstance(input, str):
        return [{"role": "user", "content": input}]
    return [i for i in (input or []) if isinstance(i, dict)]


def _message(text: str, idx: int) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id=f"msg_{idx}",
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )


class FakeModel(Model):
    def __init__(self) -> None:
        self.calls = 0

    def _decide(self, system_instructions: Optional[str], input: Any) -> list:
        self.calls += 1
        items = _items(input)
        if "evaluation judge" in (system_instructions or "").lower():
            prompt = _text_of(items[-1]) if items else ""
            verdict = "Fail" if "wrong" in prompt.lower() else "Pass"
            body = {"reasoning": f"judged {verdict}", "answer": verdict, "score": 1.0}
            return [_message(json.dumps(body), self.calls)]

        last_user = ""
        last_user_idx = -1
        for i, it in enumerate(items):
            if it.get("role") == "user":
                last_user, last_user_idx = _text_of(it), i
        tail = items[last_user_idx + 1 :]
        tool_output = next(
            (it for it in tail if it.get("type") == "function_call_output"), None
        )
        text = last_user.strip()
        low = text.lower()
        m = re.search(r"weather in ([a-z]+)", low)
        if m:
            city = m.group(1).title()
            if tool_output is None:
                return [
                    ResponseFunctionToolCall(
                        id=f"fc_{city.lower()}",
                        type="function_call",
                        name="get_weather",
                        call_id=f"call_{city.lower()}",
                        arguments=json.dumps({"city": city}),
                    )
                ]
            return [_message(f"It is sunny in {city}.", self.calls)]
        if "say nothing" in low:
            return [_message("", self.calls)]
        if "be wrong" in low:
            return [_message("This is wrong.", self.calls)]
        return [_message(f"You said: {text}", self.calls)]

    async def get_response(self, system_instructions, input, *args, **kwargs):
        output = self._decide(system_instructions, input)
        return ModelResponse(
            output=output,
            usage=Usage(requests=1, input_tokens=3, output_tokens=2, total_tokens=5),
            response_id=f"resp_{self.calls}",
        )

    async def stream_response(
        self, system_instructions, input, *args, **kwargs
    ) -> AsyncIterator[Any]:
        output = self._decide(system_instructions, input)
        yield ResponseCompletedEvent(
            type="response.completed",
            sequence_number=0,
            response=Response(
                id=f"resp_{self.calls}",
                created_at=0,
                object="response",
                model="fake",
                parallel_tool_calls=False,
                tool_choice="auto",
                tools=[],
                output=output,
                usage=ResponseUsage(
                    input_tokens=3,
                    output_tokens=2,
                    total_tokens=5,
                    input_tokens_details=InputTokensDetails(cached_tokens=0),
                    output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
                ),
            ),
        )


def install_fake_model(monkeypatch, *, prefix: Optional[str] = "fake") -> "FakeModel":
    """Answer every model request whose model name starts with ``prefix`` with one :class:`FakeModel`.

    ``prefix=None`` routes every request, whatever the provider. Patches the
    SDK's single model-resolution point, so project agents (``model:
    fake-model`` in ``agent.yml``), judges and simulated users are all
    covered without a network call.
    """
    from agents.run_internal import run_loop

    model = FakeModel()
    original = run_loop.get_model

    def get_model(agent, run_config):
        name = run_config.model if run_config.model is not None else agent.model
        if isinstance(name, Model):
            return original(agent, run_config)
        if prefix is None or (isinstance(name, str) and name.startswith(prefix)):
            return model
        return original(agent, run_config)

    monkeypatch.setattr(run_loop, "get_model", get_model)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-offline-test")
    return model
