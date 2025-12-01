"""Tests for RealtimeSession class."""

import pytest
import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch, MagicMock

from omniagents.core.agents.specs import AgentSpec, RealtimeSettings, HandoffConfig
from omniagents.core.session.audio_storage import AudioStorage
from omniagents.rpc.realtime_session import RealtimeSession, _resolve_realtime_url
from omniagents.rpc.realtime_events import (
    RealtimeAudioEvent,
    RealtimeTurnStartedEvent,
    RealtimeTurnEndedEvent,
    RealtimeHandoffEvent,
    RealtimeAgentStartEvent,
    RealtimeAgentEndEvent,
    RealtimeGuardrailTrippedEvent,
)


@pytest.fixture
def realtime_spec():
    """Create a test agent spec with realtime mode enabled."""
    return AgentSpec(
        name="TestRealtimeAgent",
        welcome_text="Welcome",
        get_agent_instructions=lambda: "Test instructions",
        build_agent=None,
        model_name="gpt-4o-realtime-preview",
        realtime_mode=True,
        realtime_settings=RealtimeSettings(
            model_name="gpt-4o-realtime-preview",
            voice="alloy",
            modalities=["audio"],
            input_audio_format="pcm16",
            output_audio_format="pcm16",
        ),
        project_slug="test_project",
        agent_slug="test_agent",
    )


@pytest.fixture
def settings_with_key():
    """Create settings dict with API key."""
    return {"openai_api_key": "sk-test-key"}


@pytest.fixture
def mock_audio_storage(tmp_path):
    """Create a mock AudioStorage instance."""
    storage = AudioStorage(base_path=tmp_path / "audio")
    return storage


# --- Initialization Tests ---


def test_realtime_session_creation(realtime_spec):
    """Test creating a RealtimeSession."""
    session = RealtimeSession(realtime_spec)

    assert session.spec == realtime_spec
    assert session.session_id is not None
    assert session.audio_storage is not None
    assert not session._active


def test_realtime_session_with_custom_session_id(realtime_spec):
    """Test creating session with custom session ID."""
    custom_id = "custom-session-123"
    session = RealtimeSession(realtime_spec, session_id=custom_id)

    assert session.session_id == custom_id


def test_realtime_session_with_custom_audio_storage(realtime_spec, mock_audio_storage):
    """Test creating session with custom AudioStorage."""
    session = RealtimeSession(realtime_spec, audio_storage=mock_audio_storage)

    assert session.audio_storage == mock_audio_storage


def test_realtime_session_requires_realtime_mode():
    """Test that non-realtime spec raises error."""
    spec = AgentSpec(
        name="TextAgent",
        welcome_text="Welcome",
        get_agent_instructions=lambda: "Test",
        build_agent=None,
        model_name="gpt-4",
        realtime_mode=False,
    )

    with pytest.raises(ValueError, match="realtime_mode=True"):
        RealtimeSession(spec)


# --- Session Lifecycle Tests ---


@pytest.mark.asyncio
async def test_start_session(realtime_spec, settings_with_key):
    """Test starting a realtime session."""
    # Patch at the module where they're imported from (with create=True since they may not exist)
    with (
        patch("agents.realtime.RealtimeAgent", create=True) as MockAgent,
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch(
            "omniagents.rpc.realtime_session.history_db.register_session"
        ) as mock_register,
    ):
        session = RealtimeSession(realtime_spec, settings=settings_with_key)
        run_id = await session.start()

        assert run_id is not None
        assert session._active
        assert session._run_id == run_id
        assert session._api_key == "sk-test-key"

        # Verify agent created with correct params (only name, instructions, tools, handoffs, output_guardrails)
        MockAgent.assert_called_once()
        call_kwargs = MockAgent.call_args[1]
        assert call_kwargs["name"] == "TestRealtimeAgent"
        assert "instructions" in call_kwargs
        assert "tools" in call_kwargs
        assert "handoffs" in call_kwargs
        assert "output_guardrails" in call_kwargs
        # Model/voice settings are NOT passed to RealtimeAgent
        assert "model" not in call_kwargs
        assert "voice" not in call_kwargs

        # Verify runner created with agent only (no API key)
        MockRunner.assert_called_once()
        call_args = MockRunner.call_args[0]
        assert len(call_args) == 1  # Only agent passed

        # Verify session registered
        mock_register.assert_called_once()


@pytest.mark.asyncio
async def test_start_session_requires_api_key(realtime_spec):
    """Test that starting session without API key raises error."""
    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True),
    ):
        session = RealtimeSession(realtime_spec, settings={})

        with pytest.raises(ValueError, match="API key"):
            await session.start()


@pytest.mark.asyncio
async def test_start_session_already_active(realtime_spec, settings_with_key):
    """Test that starting an already active session raises error."""
    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True),
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        session = RealtimeSession(realtime_spec, settings=settings_with_key)
        await session.start()

        with pytest.raises(RuntimeError, match="already active"):
            await session.start()


@pytest.mark.asyncio
async def test_connect_session(realtime_spec, settings_with_key):
    """Test connecting to a realtime session."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        session = RealtimeSession(realtime_spec, settings=settings_with_key)
        await session.start()

        rt_session = await session.connect()

        assert rt_session == mock_session
        assert session._realtime_session == mock_session

        # Verify runner.run was called with model_config
        mock_runner.run.assert_called_once()
        call_kwargs = mock_runner.run.call_args[1]
        assert "model_config" in call_kwargs
        model_config = call_kwargs["model_config"]
        assert model_config["api_key"] == "sk-test-key"
        assert "initial_model_settings" in model_config

        # Verify model settings include realtime config
        settings = model_config["initial_model_settings"]
        assert settings["modalities"] == ["audio"]
        assert settings["input_audio_format"] == "pcm16"
        assert settings["output_audio_format"] == "pcm16"

        # Verify session was entered
        mock_session.__aenter__.assert_called_once()


@pytest.mark.asyncio
async def test_connect_session_azure_headers(realtime_spec):
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        settings = {
            "openai_api_key": "azure-key",
            "openai_realtime_url": "wss://example.openai.azure.com/openai/realtime?deployment=rt",
        }
        session = RealtimeSession(realtime_spec, settings=settings)
        await session.start()
        await session.connect()

        model_config = mock_runner.run.call_args[1]["model_config"]
        assert "api_key" not in model_config
        assert model_config["headers"]["api-key"] == "azure-key"
        assert model_config["url"].startswith("wss://example.openai.azure.com")


@pytest.mark.asyncio
async def test_connect_session_query_flag_override(realtime_spec):
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        settings = {
            "openai_api_key": "proxy-key",
            "openai_realtime_url": "wss://proxy.internal/realtime",
            "openai_realtime_api_key_in_query": True,
        }
        session = RealtimeSession(realtime_spec, settings=settings)
        await session.start()
        await session.connect()

        model_config = mock_runner.run.call_args[1]["model_config"]
        assert "api_key" not in model_config
        assert "api-key=proxy-key" in model_config["url"]


@pytest.mark.asyncio
async def test_connect_session_force_header_non_azure(realtime_spec):
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        settings = {
            "openai_api_key": "custom-key",
            "openai_realtime_url": "wss://proxy.internal/realtime",
            "openai_realtime_force_api_key_header": True,
        }
        session = RealtimeSession(realtime_spec, settings=settings)
        await session.start()
        await session.connect()

        model_config = mock_runner.run.call_args[1]["model_config"]
        assert "api_key" not in model_config
        assert model_config["headers"]["api-key"] == "custom-key"


@pytest.mark.asyncio
async def test_connect_requires_start(realtime_spec, settings_with_key):
    """Test that connecting without starting raises error."""
    session = RealtimeSession(realtime_spec, settings=settings_with_key)

    with pytest.raises(RuntimeError, match="not started"):
        await session.connect()


@pytest.mark.asyncio
async def test_stop_session(realtime_spec, settings_with_key):
    """Test stopping a realtime session."""
    mock_session = AsyncMock()
    mock_session.__aexit__ = AsyncMock()

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)

        session = RealtimeSession(realtime_spec, settings=settings_with_key)
        await session.start()
        await session.connect()
        await session.stop()

        assert not session._active
        assert session._realtime_session is None
        assert session._runner is None
        assert session._agent is None

        # Verify session was properly exited
        mock_session.__aexit__.assert_called_once()


# --- Event Streaming Tests ---


@pytest.mark.asyncio
async def test_stream_events(realtime_spec, settings_with_key):
    """Test streaming events from realtime session."""

    async def mock_events():
        """Mock event generator."""
        yield SimpleNamespace(type="agent_start", agent=SimpleNamespace(name="test"))
        yield SimpleNamespace(type="agent_end", agent=SimpleNamespace(name="test"))

    mock_session = AsyncMock()
    mock_session.__aiter__ = lambda self: mock_events()

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)

        session = RealtimeSession(realtime_spec, settings=settings_with_key)
        await session.start()
        await session.connect()

        events = []
        async for event in session.stream_events():
            events.append(event)

        assert len(events) == 2
        assert isinstance(events[0], RealtimeAgentStartEvent)
        assert isinstance(events[1], RealtimeAgentEndEvent)


@pytest.mark.asyncio
async def test_stream_events_with_audio(realtime_spec, settings_with_key, tmp_path):
    """Test streaming events with audio storage."""
    audio_data = b"test_audio_data"
    audio_base64 = base64.b64encode(audio_data).decode("utf-8")

    async def mock_events():
        """Mock event generator with audio."""
        yield SimpleNamespace(
            type="audio",
            audio=SimpleNamespace(data=audio_data),
            item_id="item_123",
            content_index=0,
        )

    mock_session = AsyncMock()
    mock_session.__aiter__ = lambda self: mock_events()

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
        patch(
            "omniagents.rpc.realtime_session.history_db.save_audio_metadata"
        ) as mock_save_metadata,
    ):
        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)

        storage = AudioStorage(base_path=tmp_path / "audio")
        session = RealtimeSession(
            realtime_spec, settings=settings_with_key, audio_storage=storage
        )
        await session.start()
        await session.connect()

        events = []
        async for event in session.stream_events():
            events.append(event)

        assert len(events) == 1
        assert isinstance(events[0], RealtimeAudioEvent)
        assert events[0].audio_base64 == audio_base64

        # Verify audio was stored
        mock_save_metadata.assert_called_once()


@pytest.mark.asyncio
async def test_stream_events_requires_connection(realtime_spec, settings_with_key):
    """Test that streaming events without connection raises error."""
    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True),
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        session = RealtimeSession(realtime_spec, settings=settings_with_key)
        await session.start()

        with pytest.raises(RuntimeError, match="not connected"):
            async for _ in session.stream_events():
                pass


# --- Audio/Text Input Tests ---


@pytest.mark.asyncio
async def test_send_audio(realtime_spec, settings_with_key):
    """Test sending audio to realtime session."""
    audio_data = b"test_audio"
    audio_base64 = base64.b64encode(audio_data).decode("utf-8")

    mock_session = AsyncMock()
    mock_session.send_audio = AsyncMock()

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)

        session = RealtimeSession(realtime_spec, settings=settings_with_key)
        await session.start()
        await session.connect()

        await session.send_audio(audio_base64)

        mock_session.send_audio.assert_called_once_with(audio_data, commit=False)


@pytest.mark.asyncio
async def test_send_text(realtime_spec, settings_with_key):
    """Test sending text to realtime session."""
    mock_session = AsyncMock()
    mock_session.send_message = AsyncMock()

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)

        session = RealtimeSession(realtime_spec, settings=settings_with_key)
        await session.start()
        await session.connect()

        await session.send_text("Hello")

        mock_session.send_message.assert_called_once_with("Hello")


@pytest.mark.asyncio
async def test_interrupt(realtime_spec, settings_with_key):
    """Test interrupting agent response."""
    mock_session = AsyncMock()
    mock_session.interrupt = AsyncMock()

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)

        session = RealtimeSession(realtime_spec, settings=settings_with_key)
        await session.start()
        await session.connect()

        await session.interrupt()

        mock_session.interrupt.assert_called_once()


# --- Context Manager Tests ---


@pytest.mark.asyncio
async def test_managed_context_manager(realtime_spec, settings_with_key):
    """Test using session as context manager."""
    mock_session = AsyncMock()
    mock_session.__aexit__ = AsyncMock()

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)

        session = RealtimeSession(realtime_spec, settings=settings_with_key)

        async with session.managed():
            assert session._active
            await session.connect()

        # Verify cleanup happened
        assert not session._active
        mock_session.__aexit__.assert_called_once()


@pytest.mark.asyncio
async def test_managed_context_manager_exception_handling(
    realtime_spec, settings_with_key
):
    """Test context manager cleanup on exception."""
    mock_session = AsyncMock()
    mock_session.__aexit__ = AsyncMock()

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)

        session = RealtimeSession(realtime_spec, settings=settings_with_key)

        with pytest.raises(ValueError):
            async with session.managed():
                await session.connect()
                raise ValueError("Test error")

        # Verify cleanup still happened
        assert not session._active
        mock_session.__aexit__.assert_called_once()


# --- Handoff Tests ---


@pytest.mark.asyncio
async def test_start_session_with_handoffs(settings_with_key):
    """Test starting a session with handoff configurations."""
    # Create handoff agent spec
    handoff_spec = AgentSpec(
        name="HandoffAgent",
        welcome_text="Welcome",
        get_agent_instructions=lambda: "Handoff agent instructions",
        build_agent=None,
        model_name="gpt-4o",
        realtime_mode=False,  # Handoff agents can be regular agents
    )

    # Create main spec with handoff config
    main_spec = AgentSpec(
        name="MainRealtimeAgent",
        welcome_text="Welcome",
        get_agent_instructions=lambda: "Main agent instructions",
        build_agent=None,
        model_name="gpt-4o-realtime-preview",
        realtime_mode=True,
        realtime_settings=RealtimeSettings(
            model_name="gpt-4o-realtime-preview",
            voice="alloy",
        ),
        handoff_configs=[HandoffConfig(inline_spec=handoff_spec)],
        project_slug="test_project",
        agent_slug="test_agent",
    )

    with (
        patch("agents.realtime.RealtimeAgent", create=True) as MockRealtimeAgent,
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
        patch("agents.Agent", create=True) as MockAgent,
        patch("agents.handoff", create=True) as mock_handoff,
    ):
        # Setup mocks
        mock_handoff_agent = Mock()
        MockAgent.return_value = mock_handoff_agent

        mock_handoff_obj = Mock()
        mock_handoff.return_value = mock_handoff_obj

        session = RealtimeSession(main_spec, settings=settings_with_key)
        run_id = await session.start()

        # Verify handoff was built and passed to RealtimeAgent
        MockRealtimeAgent.assert_called_once()
        call_kwargs = MockRealtimeAgent.call_args[1]
        assert "handoffs" in call_kwargs
        assert len(call_kwargs["handoffs"]) == 1
        assert call_kwargs["handoffs"][0] == mock_handoff_obj


@pytest.mark.asyncio
async def test_stream_handoff_events(settings_with_key):
    """Test streaming handoff-related events."""
    # Create simple spec
    spec = AgentSpec(
        name="TestAgent",
        welcome_text="Welcome",
        get_agent_instructions=lambda: "Test",
        build_agent=None,
        model_name="gpt-4o-realtime-preview",
        realtime_mode=True,
        realtime_settings=RealtimeSettings(),
        project_slug="test_project",
        agent_slug="test_agent",
    )

    async def mock_events():
        """Mock event generator with handoff events."""
        yield SimpleNamespace(
            type="agent_start", agent=SimpleNamespace(name="specialist")
        )
        yield SimpleNamespace(
            type="handoff",
            from_agent=SimpleNamespace(name="main"),
            to_agent=SimpleNamespace(name="specialist"),
        )
        yield SimpleNamespace(
            type="agent_end", agent=SimpleNamespace(name="specialist")
        )

    mock_session = AsyncMock()
    mock_session.__aiter__ = lambda self: mock_events()

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)

        session = RealtimeSession(spec, settings=settings_with_key)
        await session.start()
        await session.connect()

        events = []
        async for event in session.stream_events():
            events.append(event)

        assert len(events) == 3
        assert isinstance(events[0], RealtimeAgentStartEvent)
        assert events[0].agent_name == "specialist"
        assert isinstance(events[1], RealtimeHandoffEvent)
        assert events[1].from_agent == "main"
        assert events[1].to_agent == "specialist"
        assert isinstance(events[2], RealtimeAgentEndEvent)
        assert events[2].agent_name == "specialist"


@pytest.mark.asyncio
async def test_start_session_with_tools_and_handoffs(settings_with_key):
    """Test that both tools and handoffs are passed to RealtimeAgent."""

    def test_tool():
        """Test tool."""
        return "test"

    handoff_spec = AgentSpec(
        name="HandoffAgent",
        welcome_text="Welcome",
        get_agent_instructions=lambda: "Handoff agent",
        build_agent=None,
        model_name="gpt-4o",
        realtime_mode=False,
    )

    main_spec = AgentSpec(
        name="MainAgent",
        welcome_text="Welcome",
        get_agent_instructions=lambda: "Main agent",
        build_agent=None,
        model_name="gpt-4o-realtime-preview",
        realtime_mode=True,
        realtime_settings=RealtimeSettings(),
        available_tools=[test_tool],
        handoff_configs=[HandoffConfig(inline_spec=handoff_spec)],
        project_slug="test_project",
        agent_slug="test_agent",
    )

    with (
        patch("agents.realtime.RealtimeAgent", create=True) as MockRealtimeAgent,
        patch("agents.realtime.RealtimeRunner", create=True),
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
        patch("agents.Agent", create=True),
        patch("agents.handoff", create=True) as mock_handoff,
    ):
        mock_handoff.return_value = Mock()

        session = RealtimeSession(main_spec, settings=settings_with_key)
        await session.start()

        # Verify both tools and handoffs were passed
        MockRealtimeAgent.assert_called_once()
        call_kwargs = MockRealtimeAgent.call_args[1]
        assert "tools" in call_kwargs
        assert len(call_kwargs["tools"]) == 1
        assert call_kwargs["tools"][0] == test_tool
        assert "handoffs" in call_kwargs
        assert len(call_kwargs["handoffs"]) == 1


# --- Guardrail Tests ---


@pytest.mark.asyncio
async def test_start_session_with_guardrails(settings_with_key):
    """Test starting a session with guardrails."""
    # Create mock guardrails
    mock_input_guardrail = Mock()
    mock_input_guardrail.get_name = Mock(return_value="input_guardrail")

    mock_output_guardrail = Mock()
    mock_output_guardrail.get_name = Mock(return_value="output_guardrail")

    # Create spec with guardrails
    spec = AgentSpec(
        name="GuardrailAgent",
        welcome_text="Welcome",
        get_agent_instructions=lambda: "Agent with guardrails",
        build_agent=None,
        model_name="gpt-4o-realtime-preview",
        realtime_mode=True,
        realtime_settings=RealtimeSettings(),
        input_guardrails=[mock_input_guardrail],
        output_guardrails=[mock_output_guardrail],
        project_slug="test_project",
        agent_slug="test_agent",
    )

    with (
        patch("agents.realtime.RealtimeAgent", create=True) as MockRealtimeAgent,
        patch("agents.realtime.RealtimeRunner", create=True),
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        session = RealtimeSession(spec, settings=settings_with_key)
        await session.start()

        # Verify guardrails were passed to RealtimeAgent
        MockRealtimeAgent.assert_called_once()
        call_kwargs = MockRealtimeAgent.call_args[1]
        # Input guardrails are not passed to RealtimeAgent constructor
        assert "output_guardrails" in call_kwargs
        assert len(call_kwargs["output_guardrails"]) == 1
        assert call_kwargs["output_guardrails"][0] == mock_output_guardrail


def test_resolve_realtime_url_non_azure_returns_none(monkeypatch, realtime_spec):
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    resolved = _resolve_realtime_url(
        explicit_url=None,
        base_url="https://api.openai.com/v1",
        spec=realtime_spec,
        settings={},
    )
    assert resolved is None


def test_resolve_realtime_url_keeps_explicit(monkeypatch, realtime_spec):
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    explicit = "wss://proxy.internal/realtime"
    resolved = _resolve_realtime_url(
        explicit_url=explicit,
        base_url=None,
        spec=realtime_spec,
        settings={},
    )
    assert resolved == explicit


def test_resolve_realtime_url_builds_azure_path(monkeypatch, realtime_spec):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://cs-hackr.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-realtime")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-05-01-preview")
    resolved = _resolve_realtime_url(
        explicit_url=None,
        base_url="https://cs-hackr.openai.azure.com/openai/v1/",
        spec=realtime_spec,
        settings={},
    )
    assert (
        resolved
        == "wss://cs-hackr.openai.azure.com/openai/deployments/gpt-realtime/realtime?api-version=2024-05-01-preview"
    )


def test_resolve_realtime_url_respects_explicit_azure_query(monkeypatch, realtime_spec):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://cs-hackr.openai.azure.com/")
    explicit = "wss://cs-hackr.openai.azure.com/openai/deployments/foo/realtime?api-version=2024-05-01-preview"
    resolved = _resolve_realtime_url(
        explicit_url=explicit,
        base_url=None,
        spec=realtime_spec,
        settings={},
    )
    assert resolved == explicit


@pytest.mark.asyncio
async def test_stream_guardrail_tripped_event(settings_with_key):
    """Test streaming guardrail_tripped events."""
    spec = AgentSpec(
        name="TestAgent",
        welcome_text="Welcome",
        get_agent_instructions=lambda: "Test",
        build_agent=None,
        model_name="gpt-4o-realtime-preview",
        realtime_mode=True,
        realtime_settings=RealtimeSettings(),
        project_slug="test_project",
        agent_slug="test_agent",
    )

    async def mock_events():
        """Mock event generator with guardrail event (real SDK structure)."""
        # SDK RealtimeGuardrailTripped has guardrail_results (list) and message
        guardrail_result = SimpleNamespace(
            guardrail=SimpleNamespace(name="content_filter"),
            agent_output="Inappropriate content detected",
        )
        yield SimpleNamespace(
            type="guardrail_tripped",
            guardrail_results=[guardrail_result],
            message="Output was blocked by guardrail",
        )

    mock_session = AsyncMock()
    mock_session.__aiter__ = lambda self: mock_events()

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)

        session = RealtimeSession(spec, settings=settings_with_key)
        await session.start()
        await session.connect()

        events = []
        async for event in session.stream_events():
            events.append(event)

        assert len(events) == 1
        assert isinstance(events[0], RealtimeGuardrailTrippedEvent)
        assert len(events[0].guardrail_results) == 1
        assert events[0].guardrail_results[0]["guardrail_name"] == "content_filter"
        assert "Inappropriate content" in events[0].guardrail_results[0]["agent_output"]
        assert events[0].message == "Output was blocked by guardrail"


@pytest.mark.asyncio
async def test_start_session_with_tools_handoffs_and_guardrails(settings_with_key):
    """Test that tools, handoffs, and guardrails all work together."""

    def test_tool():
        """Test tool."""
        return "test"

    mock_input_guardrail = Mock()
    mock_output_guardrail = Mock()

    handoff_spec = AgentSpec(
        name="HandoffAgent",
        welcome_text="Welcome",
        get_agent_instructions=lambda: "Handoff agent",
        build_agent=None,
        model_name="gpt-4o",
        realtime_mode=False,
    )

    main_spec = AgentSpec(
        name="MainAgent",
        welcome_text="Welcome",
        get_agent_instructions=lambda: "Main agent",
        build_agent=None,
        model_name="gpt-4o-realtime-preview",
        realtime_mode=True,
        realtime_settings=RealtimeSettings(),
        available_tools=[test_tool],
        handoff_configs=[HandoffConfig(inline_spec=handoff_spec)],
        input_guardrails=[mock_input_guardrail],
        output_guardrails=[mock_output_guardrail],
        project_slug="test_project",
        agent_slug="test_agent",
    )

    with (
        patch("agents.realtime.RealtimeAgent", create=True) as MockRealtimeAgent,
        patch("agents.realtime.RealtimeRunner", create=True),
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
        patch("agents.Agent", create=True),
        patch("agents.handoff", create=True) as mock_handoff,
    ):
        mock_handoff.return_value = Mock()

        session = RealtimeSession(main_spec, settings=settings_with_key)
        await session.start()

        # Verify all components were passed
        MockRealtimeAgent.assert_called_once()
        call_kwargs = MockRealtimeAgent.call_args[1]

        # Tools
        assert "tools" in call_kwargs
        assert len(call_kwargs["tools"]) == 1
        assert call_kwargs["tools"][0] == test_tool

        # Handoffs
        assert "handoffs" in call_kwargs
        assert len(call_kwargs["handoffs"]) == 1

        # Guardrails
        # Input guardrails are not passed to RealtimeAgent constructor
        assert "output_guardrails" in call_kwargs
        assert len(call_kwargs["output_guardrails"]) == 1
        assert call_kwargs["output_guardrails"][0] == mock_output_guardrail
