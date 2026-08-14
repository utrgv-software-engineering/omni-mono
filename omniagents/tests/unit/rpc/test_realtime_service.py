"""Tests for RealtimeService class."""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from types import SimpleNamespace

from omniagents.core.agents.specs import AgentSpec, RealtimeSettings
from omniagents.rpc.realtime_service import RealtimeService
from omniagents.rpc.realtime_events import RealtimeTurnStartedEvent


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


# --- Initialization Tests ---


def test_realtime_service_creation(realtime_spec, settings_with_key):
    """Test creating a RealtimeService."""
    service = RealtimeService(realtime_spec, settings_with_key)

    assert service.spec == realtime_spec
    assert service.settings == settings_with_key
    assert len(service._sessions) == 0
    assert len(service._event_tasks) == 0


def test_realtime_service_requires_realtime_mode():
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
        RealtimeService(spec)


# --- Session Management Tests ---


@pytest.mark.asyncio
async def test_start_session(realtime_spec, settings_with_key):
    """Test starting a realtime session."""
    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        # Setup mocks
        mock_session = AsyncMock()

        async def mock_events():
            """Empty event generator."""
            return
            yield  # Make it a generator

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        service = RealtimeService(realtime_spec, settings_with_key)

        result = await service.start_session("test-session-123")

        assert result["session_id"] == "test-session-123"
        assert "run_id" in result
        assert "test-session-123" in service._sessions
        assert "test-session-123" in service._event_tasks


@pytest.mark.asyncio
async def test_start_session_acquires_environment(realtime_spec, settings_with_key):
    """With an agent service, start_session acquires the paired GUI session's
    environment lease and hands it to the RealtimeSession."""
    lease = SimpleNamespace(
        workspace=SimpleNamespace(root="/work/repo"),
        workspace_id="ws-1",
        environment_id="env-1",
        generation=1,
    )
    agent_service = MagicMock()
    agent_service.build_realtime_client_tools = MagicMock(return_value=[])
    agent_service.acquire_environment_for_session = AsyncMock(return_value=lease)

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        mock_session = AsyncMock()

        async def mock_events():
            return
            yield

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()
        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        service = RealtimeService(
            realtime_spec, settings_with_key, agent_service=agent_service
        )
        service.default_environment_id = "env-default"
        await service.start_session("test-session-123")

        agent_service.acquire_environment_for_session.assert_awaited_once_with(
            "test-session-123", principal=None, default_environment_id="env-default"
        )
        assert service._sessions["test-session-123"].environment_lease is lease


@pytest.mark.asyncio
async def test_start_session_lease_failure_is_nonfatal(
    realtime_spec, settings_with_key
):
    """A failing environment acquisition logs and continues without a lease."""
    agent_service = MagicMock()
    agent_service.build_realtime_client_tools = MagicMock(return_value=[])
    agent_service.acquire_environment_for_session = AsyncMock(
        side_effect=RuntimeError("no binding")
    )

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        mock_session = AsyncMock()

        async def mock_events():
            return
            yield

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()
        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        service = RealtimeService(
            realtime_spec, settings_with_key, agent_service=agent_service
        )
        result = await service.start_session("test-session-123")

        assert result["session_id"] == "test-session-123"
        assert service._sessions["test-session-123"].environment_lease is None


@pytest.mark.asyncio
async def test_start_session_generates_id(realtime_spec, settings_with_key):
    """Test that session ID is generated if not provided."""
    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        # Setup mocks
        mock_session = AsyncMock()
        mock_session.session_config = {}

        async def mock_events():
            return
            yield

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        service = RealtimeService(realtime_spec, settings_with_key)

        result = await service.start_session()

        assert "session_id" in result
        assert len(result["session_id"]) > 0
        assert result["session_id"] in service._sessions


@pytest.mark.asyncio
async def test_start_session_already_exists(realtime_spec, settings_with_key):
    """Test that starting duplicate session raises error."""
    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        # Setup mocks
        mock_session = AsyncMock()
        mock_session.session_config = {}

        async def mock_events():
            return
            yield

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        service = RealtimeService(realtime_spec, settings_with_key)

        await service.start_session("test-session")

        with pytest.raises(RuntimeError, match="already exists"):
            await service.start_session("test-session")


@pytest.mark.asyncio
async def test_stop_session(realtime_spec, settings_with_key):
    """Test stopping a realtime session."""
    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        # Setup mocks
        mock_session = AsyncMock()
        mock_session.session_config = {}
        mock_session.close = AsyncMock()

        async def mock_events():
            return
            yield

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)
        mock_runner.stop = AsyncMock()

        service = RealtimeService(realtime_spec, settings_with_key)

        result = await service.start_session("test-session")
        session_id = result["session_id"]

        # Give event task a moment to start
        await asyncio.sleep(0.1)

        success = await service.stop_session(session_id)

        assert success is True
        assert session_id not in service._sessions
        assert session_id not in service._event_tasks


@pytest.mark.asyncio
async def test_stop_session_not_found(realtime_spec, settings_with_key):
    """Test stopping non-existent session raises error."""
    service = RealtimeService(realtime_spec, settings_with_key)

    with pytest.raises(ValueError, match="not found"):
        await service.stop_session("nonexistent")


# --- Audio/Text Input Tests ---


@pytest.mark.asyncio
async def test_send_audio(realtime_spec, settings_with_key):
    """Test sending audio to a session."""
    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        # Setup mocks
        mock_session = AsyncMock()
        mock_session.session_config = {}
        mock_session.input_audio_buffer = AsyncMock()
        mock_session.input_audio_buffer.append = AsyncMock()

        async def mock_events():
            return
            yield

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        service = RealtimeService(realtime_spec, settings_with_key)

        result = await service.start_session("test-session")
        session_id = result["session_id"]

        success = await service.send_audio(session_id, "dGVzdCBhdWRpbw==")

        assert success is True


@pytest.mark.asyncio
async def test_send_audio_session_not_found(realtime_spec, settings_with_key):
    """Test sending audio to non-existent session raises error."""
    service = RealtimeService(realtime_spec, settings_with_key)

    with pytest.raises(ValueError, match="not found"):
        await service.send_audio("nonexistent", "audio_data")


@pytest.mark.asyncio
async def test_send_text(realtime_spec, settings_with_key):
    """Test sending text to a session."""
    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        # Setup mocks
        mock_session = AsyncMock()
        mock_session.session_config = {}
        mock_session.conversation = AsyncMock()
        mock_session.conversation.add_item = AsyncMock()
        mock_session.create_response = AsyncMock()

        async def mock_events():
            return
            yield

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        service = RealtimeService(realtime_spec, settings_with_key)

        result = await service.start_session("test-session")
        session_id = result["session_id"]

        success = await service.send_text(session_id, "Hello")

        assert success is True


@pytest.mark.asyncio
async def test_send_text_session_not_found(realtime_spec, settings_with_key):
    """Test sending text to non-existent session raises error."""
    service = RealtimeService(realtime_spec, settings_with_key)

    with pytest.raises(ValueError, match="not found"):
        await service.send_text("nonexistent", "text")


@pytest.mark.asyncio
async def test_interrupt(realtime_spec, settings_with_key):
    """Test interrupting agent response."""
    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        # Setup mocks
        mock_session = AsyncMock()
        mock_session.session_config = {}
        mock_session.cancel_response = AsyncMock()

        async def mock_events():
            return
            yield

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        service = RealtimeService(realtime_spec, settings_with_key)

        result = await service.start_session("test-session")
        session_id = result["session_id"]

        success = await service.interrupt(session_id)

        assert success is True


@pytest.mark.asyncio
async def test_interrupt_session_not_found(realtime_spec, settings_with_key):
    """Test interrupting non-existent session raises error."""
    service = RealtimeService(realtime_spec, settings_with_key)

    with pytest.raises(ValueError, match="not found"):
        await service.interrupt("nonexistent")


# --- Session Info Tests ---


@pytest.mark.asyncio
async def test_get_session_info(realtime_spec, settings_with_key):
    """Test getting session information."""
    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        # Setup mocks
        mock_session = AsyncMock()
        mock_session.session_config = {}

        async def mock_events():
            return
            yield

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        service = RealtimeService(realtime_spec, settings_with_key)

        result = await service.start_session("test-session")
        session_id = result["session_id"]
        run_id = result["run_id"]

        info = await service.get_session_info(session_id)

        assert info["session_id"] == session_id
        assert info["run_id"] == run_id
        assert info["active"] is True


@pytest.mark.asyncio
async def test_get_session_info_not_found(realtime_spec, settings_with_key):
    """Test getting info for non-existent session raises error."""
    service = RealtimeService(realtime_spec, settings_with_key)

    with pytest.raises(ValueError, match="not found"):
        await service.get_session_info("nonexistent")


@pytest.mark.asyncio
async def test_list_sessions(realtime_spec, settings_with_key):
    """Test listing all sessions."""
    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        # Setup mocks
        mock_session = AsyncMock()
        mock_session.session_config = {}

        async def mock_events():
            return
            yield

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        service = RealtimeService(realtime_spec, settings_with_key)

        # Start multiple sessions
        await service.start_session("session-1")
        await service.start_session("session-2")

        result = await service.list_sessions()

        assert len(result["sessions"]) == 2
        session_ids = [s["session_id"] for s in result["sessions"]]
        assert "session-1" in session_ids
        assert "session-2" in session_ids


# --- Event Streaming Tests ---


@pytest.mark.asyncio
async def test_event_streaming(realtime_spec, settings_with_key):
    """Test that events are streamed to client."""
    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        # Setup mocks
        mock_session = AsyncMock()
        mock_session.session_config = {}

        # Event generator with actual events
        async def mock_events():
            yield SimpleNamespace(
                type="agent_start", agent=SimpleNamespace(name="test")
            )
            yield SimpleNamespace(type="agent_end", agent=SimpleNamespace(name="test"))

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)

        # Mock channel to capture events
        mock_channel = AsyncMock()
        event_calls = []

        async def capture_event(method, event_dict):
            event_calls.append((method, event_dict))

        mock_channel.async_call = capture_event

        service = RealtimeService(realtime_spec, settings_with_key)
        service.channel = mock_channel

        result = await service.start_session("test-session")

        # Wait for events to be streamed
        await asyncio.sleep(0.2)

        # Verify events were sent
        assert len(event_calls) == 2
        assert event_calls[0][0] == "realtime_event"
        assert event_calls[0][1]["type"] == "realtime_agent_start"
        assert event_calls[1][0] == "realtime_event"
        assert event_calls[1][1]["type"] == "realtime_agent_end"


# --- Cleanup Tests ---


@pytest.mark.asyncio
async def test_cleanup(realtime_spec, settings_with_key):
    """Test cleanup of all sessions."""
    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        # Setup mocks
        mock_session = AsyncMock()
        mock_session.session_config = {}
        mock_session.close = AsyncMock()

        async def mock_events():
            return
            yield

        mock_session.__aiter__ = lambda self: mock_events()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_runner = MockRunner.return_value
        mock_runner.run = AsyncMock(return_value=mock_session)
        mock_runner.stop = AsyncMock()

        service = RealtimeService(realtime_spec, settings_with_key)

        # Start multiple sessions
        await service.start_session("session-1")
        await service.start_session("session-2")

        assert len(service._sessions) == 2

        await service.cleanup()

        assert len(service._sessions) == 0
        assert len(service._event_tasks) == 0


class TestCapabilities:
    """`capabilities()` must reflect a real credential.

    Clients gate the mic button on `enabled`, so a hardcoded True renders a
    control that fails on click.
    """

    @pytest.mark.asyncio
    async def test_enabled_with_explicit_api_key(
        self, realtime_spec, settings_with_key
    ):
        caps = await RealtimeService(realtime_spec, settings_with_key).capabilities()
        assert caps["enabled"] is True
        assert caps["detail"] == "settings api key"
        assert caps["kind"] == "realtime"

    @pytest.mark.asyncio
    async def test_enabled_via_chatgpt_oauth(self, realtime_spec):
        with patch(
            "omniagents.core.providers.codex.realtime_signin_state",
            return_value="ok",
        ):
            caps = await RealtimeService(realtime_spec, {}).capabilities()
        assert caps["enabled"] is True
        assert "ChatGPT OAuth" in caps["detail"]

    @pytest.mark.asyncio
    async def test_disabled_when_signed_out_and_no_key(self, realtime_spec):
        with patch(
            "omniagents.core.providers.codex.realtime_signin_state",
            return_value="missing",
        ):
            caps = await RealtimeService(realtime_spec, {}).capabilities()
        assert caps["enabled"] is False
        assert "not signed in" in caps["detail"]

    @pytest.mark.asyncio
    async def test_disabled_when_signin_is_broken(self, realtime_spec):
        """A stamped auth_error means re-auth, not a silently dead mic."""
        with patch(
            "omniagents.core.providers.codex.realtime_signin_state",
            return_value="broken",
        ):
            caps = await RealtimeService(realtime_spec, {}).capabilities()
        assert caps["enabled"] is False
        assert "rejected" in caps["detail"]

    @pytest.mark.asyncio
    async def test_probe_never_refreshes_a_token(self, realtime_spec):
        """The probe runs on every client boot; refreshing there would spend a
        refresh token and stamp the store broken on rejection."""
        refresh = AsyncMock(return_value="eyJ-token")
        with (
            patch("omniagents.core.providers.codex.realtime_access_token", refresh),
            patch(
                "omniagents.core.providers.codex.realtime_signin_state",
                return_value="ok",
            ),
        ):
            caps = await RealtimeService(realtime_spec, {}).capabilities()
        assert caps["enabled"] is True
        refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_disabled_for_non_openai_endpoint_without_key(self, realtime_spec):
        """An Azure target must not fall back to a ChatGPT bearer."""
        signin = MagicMock(return_value="ok")
        with patch("omniagents.core.providers.codex.realtime_signin_state", signin):
            caps = await RealtimeService(
                realtime_spec, {"openai_base_url": "https://myco.openai.azure.com/"}
            ).capabilities()
        assert caps["enabled"] is False
        assert "non-OpenAI realtime endpoint" in caps["detail"]
        signin.assert_not_called()

    @pytest.mark.asyncio
    async def test_refresh_failure_reports_instead_of_raising(self, realtime_spec):
        """Refresh happens when opening a session, not when probing — so this
        is asserted against the resolver, which is the thing that refreshes."""
        from omniagents.rpc.realtime_session import resolve_realtime_credential

        with (
            patch(
                "omniagents.core.providers.codex.realtime_signin_state",
                return_value="ok",
            ),
            patch(
                "omniagents.core.providers.codex.realtime_access_token",
                AsyncMock(side_effect=RuntimeError("refresh rejected: 401")),
            ),
        ):
            token, detail = await resolve_realtime_credential({})
        assert token is None
        assert "could not be refreshed" in detail

    @pytest.mark.asyncio
    async def test_uses_settings_resolver_when_present(self, realtime_spec):
        """The probe must see the same settings a session would."""
        service = RealtimeService(
            realtime_spec,
            settings={},
            settings_resolver=lambda: {"openai_api_key": "sk-resolved"},
        )
        caps = await service.capabilities()
        assert caps["enabled"] is True
        assert caps["detail"] == "settings api key"

    @pytest.mark.asyncio
    async def test_falls_back_when_resolver_raises(
        self, realtime_spec, settings_with_key
    ):
        def boom():
            raise RuntimeError("resolver down")

        service = RealtimeService(
            realtime_spec, settings=settings_with_key, settings_resolver=boom
        )
        caps = await service.capabilities()
        assert caps["enabled"] is True


# --- Client tool bridging ---


class _StreamStub:
    """RealtimeSession stand-in: starts, streams nothing, stops."""

    def __init__(self):
        self.start = AsyncMock(return_value="run-stub")
        self.connect = AsyncMock()
        self.stop = AsyncMock()
        self._run_id = "run-stub"
        self._active = True

    async def stream_events(self):
        return
        yield  # pragma: no cover — makes this an (empty) async generator


@pytest.mark.asyncio
async def test_start_session_bridges_client_tools(realtime_spec, settings_with_key):
    sentinel_tools = [object()]
    agent_service = MagicMock()
    agent_service.build_realtime_client_tools = MagicMock(return_value=sentinel_tools)
    service = RealtimeService(
        realtime_spec, settings_with_key, agent_service=agent_service
    )

    with patch(
        "omniagents.rpc.realtime_service.RealtimeSession",
        return_value=_StreamStub(),
    ) as session_cls:
        await service.start_session("gui-sess-1")

    agent_service.build_realtime_client_tools.assert_called_once_with(
        "gui-sess-1", principal=None
    )
    assert session_cls.call_args.kwargs["extra_tools"] is sentinel_tools

    await service.stop_session("gui-sess-1")


@pytest.mark.asyncio
async def test_start_session_without_agent_service(realtime_spec, settings_with_key):
    service = RealtimeService(realtime_spec, settings_with_key)

    with patch(
        "omniagents.rpc.realtime_service.RealtimeSession",
        return_value=_StreamStub(),
    ) as session_cls:
        await service.start_session("gui-sess-2")

    assert session_cls.call_args.kwargs["extra_tools"] == []

    await service.stop_session("gui-sess-2")


@pytest.mark.asyncio
async def test_start_session_survives_bridge_failure(realtime_spec, settings_with_key):
    agent_service = MagicMock()
    agent_service.build_realtime_client_tools = MagicMock(
        side_effect=RuntimeError("boom")
    )
    service = RealtimeService(
        realtime_spec, settings_with_key, agent_service=agent_service
    )

    with patch(
        "omniagents.rpc.realtime_service.RealtimeSession",
        return_value=_StreamStub(),
    ) as session_cls:
        result = await service.start_session("gui-sess-3")

    assert result["session_id"] == "gui-sess-3"
    assert session_cls.call_args.kwargs["extra_tools"] == []

    await service.stop_session("gui-sess-3")
