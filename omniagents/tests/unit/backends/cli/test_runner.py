import types

import pytest

from omniagents.backends.cli import runner
from omniagents.backends.cli.runner import cli_approval_callback, run_cli
from omniagents.core.agents.specs import AgentSpec, SettingField


def make_spec() -> AgentSpec:
    spec = AgentSpec(
        name="UnitAgent",
        header_title="Header",
        page_title="Page",
        welcome_text="Welcome",
        client_storage_prefix="unit.",
        get_agent_instructions=lambda: "",
        build_agent=None,
        settings_fields=[
            SettingField(key="model_name", label="Model", default="unit-model"),
        ],
    )
    spec.project_slug = "proj"
    spec.agent_slug = "agent"
    return spec


class FakeItem:
    def __init__(self, item_type: str, content: str = "") -> None:
        self.type = item_type
        self.content = content

    def to_input_item(self):
        return {"role": "assistant", "content": self.content}


class FakeRun:
    def __init__(self, events):
        self._events = events

    async def stream_events(self):
        for event in self._events:
            yield event


class FakeAgent:
    def __init__(self, events):
        self._events = events
        self.calls = []
        self.kwargs = {}

    def run_streamed(self, conversation, run_config=None):
        snapshot = [dict(message) for message in conversation]
        self.calls.append((snapshot, run_config))
        return FakeRun(self._events)


@pytest.fixture
def base_spec():
    return make_spec()


@pytest.fixture(autouse=True)
def stub_cli_dependencies(monkeypatch):
    monkeypatch.setattr(runner, "apply_cli_overrides", lambda spec, args: None)
    monkeypatch.setattr(runner.Debug, "init", lambda *a, **k: None)
    monkeypatch.setattr(runner.Debug, "enabled", lambda: False)
    monkeypatch.setattr(
        runner,
        "ItemHelpers",
        types.SimpleNamespace(text_message_output=lambda item: item.content),
    )


@pytest.fixture
def session_records(monkeypatch):
    records = []

    class FakeSession:
        def __init__(self, project_slug, agent_slug, session_id=None):
            self.project_slug = project_slug
            self.agent_slug = agent_slug
            self.id = session_id or "session"
            self.history = []
            self.messages = []
            records.append(self)

        def append_message(self, message):
            self.messages.append(message)
            self.history.append(message)

    monkeypatch.setattr(runner, "Session", FakeSession)
    return records


@pytest.fixture
def artifact_callbacks(monkeypatch):
    callbacks = []

    def register(callback):
        callbacks.append(callback)

    monkeypatch.setattr(
        "omniagents.core.tools.artifacts.set_artifact_callback",
        register,
    )
    return callbacks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_inputs, expected",
    [
        (["1"], (True, False, None)),
        (["2"], (True, True, None)),
        (["3"], (False, False, "User rejected tool call")),
    ],
)
async def test_cli_approval_callback_choices(monkeypatch, user_inputs, expected):
    iterator = iter(user_inputs)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(iterator))
    result = await cli_approval_callback("tool", "{}", {})
    assert result == expected


@pytest.mark.asyncio
async def test_cli_approval_callback_reprompts(monkeypatch):
    iterator = iter(["invalid", "1"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(iterator))
    result = await cli_approval_callback("tool", "{}", {})
    assert result == (True, False, None)


@pytest.mark.asyncio
async def test_cli_approval_callback_interrupt(monkeypatch):
    def raise_interrupt(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", raise_interrupt)
    result = await cli_approval_callback("tool", "{}", {})
    assert result == (False, False, "Operation cancelled by user")


@pytest.mark.asyncio
async def test_run_cli_handles_conversation(
    monkeypatch, base_spec, session_records, artifact_callbacks
):
    message_item = FakeItem("message_output_item", "assistant reply")
    event = types.SimpleNamespace(type="run_item_stream_event", item=message_item)
    agent = FakeAgent([event])

    async def create_agent(**kwargs):
        agent.kwargs = kwargs
        return agent

    monkeypatch.setattr(runner, "create_unified_agent", create_agent)

    inputs = iter(["hello", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    args = types.SimpleNamespace(debug=False, session_id=None)
    await run_cli(spec=base_spec, config_path=None, args=args)

    assert agent.calls
    conversation, run_config = agent.calls[0]
    assert conversation == [{"role": "user", "content": "hello"}]
    assert run_config is not None

    session = session_records[0]
    assert [message["content"] for message in session.messages] == [
        "hello",
        "assistant reply",
    ]

    assert artifact_callbacks
    assert base_spec.model_name == "unit-model"
    assert agent.kwargs["approval_callback"] is runner.cli_approval_callback


@pytest.mark.asyncio
async def test_run_cli_respects_safe_agent_skip(
    monkeypatch, session_records, artifact_callbacks
):
    spec = make_spec()
    spec.safe_agent_options.skip_approvals = True
    agent = FakeAgent([])

    async def create_agent(**kwargs):
        agent.kwargs = kwargs
        return agent

    monkeypatch.setattr(runner, "create_unified_agent", create_agent)
    monkeypatch.setattr("builtins.input", lambda prompt="": "exit")

    args = types.SimpleNamespace(debug=False, session_id=None)
    await run_cli(spec=spec, config_path=None, args=args)

    assert agent.kwargs["approval_callback"] is runner.SafeAgent.auto_approval_callback


@pytest.mark.asyncio
async def test_run_cli_requires_config_path(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "exit")
    with pytest.raises(SystemExit) as exc_info:
        await run_cli(spec=None, config_path=None, args=None)
    assert exc_info.value.code == 1


@pytest.mark.asyncio
async def test_run_cli_loads_spec_from_yaml(
    monkeypatch, session_records, artifact_callbacks
):
    loaded_spec = make_spec()
    load_calls = []

    def load_spec(path):
        load_calls.append(path)
        return loaded_spec

    monkeypatch.setattr(runner, "load_agent_spec_from_yaml", load_spec)

    agent = FakeAgent([])

    async def create_agent(**kwargs):
        agent.kwargs = kwargs
        return agent

    monkeypatch.setattr(runner, "create_unified_agent", create_agent)
    monkeypatch.setattr("builtins.input", lambda prompt="": "exit")

    args = types.SimpleNamespace(debug=False, session_id=None)
    await run_cli(spec=None, config_path="config.yaml", args=args)

    assert load_calls == ["config.yaml"]
    assert agent.kwargs["spec"] is loaded_spec
