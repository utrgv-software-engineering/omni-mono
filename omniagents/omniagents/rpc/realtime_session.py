"""Server-side realtime session management for voice agents.

This module provides the RealtimeSession class for managing realtime agent
sessions on the server side, including event streaming, audio storage, and
lifecycle management.
"""

import asyncio
import uuid
import base64
from typing import Optional, Dict, Any, AsyncIterator, Callable
from contextlib import asynccontextmanager

from omniagents.core.agents.specs import AgentSpec
from omniagents.core.session.audio_storage import AudioStorage
from omniagents.core.session import history_db
from omniagents.core.paths import get_audio_storage_path
from omniagents.rpc.realtime_events import (
    RealtimeEventBase,
    map_realtime_session_event_to_rpc,
)


class RealtimeSession:
    """Manages a realtime agent session on the server side.

    This class:
    - Creates and manages a realtime agent from the OpenAI SDK
    - Streams events from the agent and converts them to RPC events
    - Stores audio chunks to disk and metadata to database
    - Manages session lifecycle (start, stop, cleanup)
    """

    def __init__(
        self,
        spec: AgentSpec,
        settings: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        audio_storage: Optional[AudioStorage] = None,
    ):
        """Initialize a realtime session.

        Args:
            spec: Agent specification with realtime settings
            settings: API keys and other settings
            session_id: Optional session ID (generated if not provided)
            audio_storage: Optional AudioStorage instance (created if not provided)
        """
        if not spec.realtime_mode:
            raise ValueError("AgentSpec must have realtime_mode=True")

        self.spec = spec
        self.settings = settings or {}
        self.session_id = session_id or str(uuid.uuid4())

        # Extract project and agent slugs for database storage
        self._project_slug = getattr(spec, "project_slug", None)
        self._agent_slug = getattr(spec, "agent_slug", None)

        # Initialize audio storage
        if audio_storage:
            self.audio_storage = audio_storage
        else:
            audio_path = get_audio_storage_path(self._project_slug, self._agent_slug)
            self.audio_storage = AudioStorage(base_path=audio_path)

        # Session state
        self._agent = None
        self._runner = None
        self._realtime_session = None
        self._run_id: Optional[str] = None
        self._api_key: Optional[str] = None
        self._active = False
        self._event_task: Optional[asyncio.Task] = None

    async def start(self) -> str:
        """Start the realtime session.

        Returns:
            The run ID for this session

        Raises:
            RuntimeError: If session is already active
        """
        if self._active:
            raise RuntimeError("Session is already active")

        # Import OpenAI SDK components
        try:
            from agents.realtime import RealtimeAgent, RealtimeRunner
        except ImportError:
            raise ImportError(
                "OpenAI agents SDK not installed. "
                "Install with: pip install openai-agents"
            )

        # Get API key
        api_key = self.settings.get("openai_api_key") or self.settings.get("api_key")
        if not api_key:
            raise ValueError("OpenAI API key not provided in settings")

        # Build tools if available
        tools = self.spec.available_tools if self.spec.available_tools else []

        # Build handoffs if configured
        handoffs = []
        if self.spec.handoff_configs:
            from omniagents.core.agents.builder import _build_handoffs_from_configs

            handoffs = await _build_handoffs_from_configs(
                handoff_configs=self.spec.handoff_configs,
                settings=self.settings,
                approval_callback=None,  # No approval callback for realtime
            )

        # Get guardrails from spec
        output_guardrails = (
            self.spec.output_guardrails if self.spec.output_guardrails else []
        )

        # Create agent with realtime configuration
        # RealtimeAgent only takes: name, instructions, tools, handoffs, output_guardrails, hooks
        self._agent = RealtimeAgent(
            name=self.spec.name or "RealtimeAgent",
            instructions=(
                self.spec.get_agent_instructions()
                if self.spec.get_agent_instructions
                else ""
            ),
            tools=tools,
            handoffs=handoffs,
            output_guardrails=output_guardrails,
        )

        # Create runner with agent
        self._runner = RealtimeRunner(self._agent)

        # Store API key for model_config
        self._api_key = api_key

        # Generate run ID
        self._run_id = str(uuid.uuid4())

        # Register session in database
        history_db.register_session(
            self.session_id,
            project_slug=self._project_slug,
            agent_slug=self._agent_slug,
        )

        self._active = True
        return self._run_id

    async def connect(self) -> Any:
        """Connect to the realtime session.

        Returns:
            The OpenAI RealtimeSession object

        Raises:
            RuntimeError: If session not started
        """
        if not self._active:
            raise RuntimeError("Session not started. Call start() first.")

        # Build model configuration
        realtime_settings = self.spec.realtime_settings

        # Build initial model settings from spec
        initial_model_settings = {}

        if realtime_settings.model_name:
            initial_model_settings["model_name"] = realtime_settings.model_name

        if realtime_settings.modalities:
            initial_model_settings["modalities"] = realtime_settings.modalities

        if realtime_settings.voice:
            initial_model_settings["voice"] = realtime_settings.voice

        if realtime_settings.temperature is not None:
            # Note: temperature is not in RealtimeSessionModelSettings
            # It may be part of a different config or not supported
            pass

        if realtime_settings.input_audio_format:
            initial_model_settings["input_audio_format"] = (
                realtime_settings.input_audio_format
            )

        if realtime_settings.output_audio_format:
            initial_model_settings["output_audio_format"] = (
                realtime_settings.output_audio_format
            )

        if realtime_settings.turn_detection:
            initial_model_settings["turn_detection"] = realtime_settings.turn_detection

        if realtime_settings.input_audio_transcription:
            initial_model_settings["input_audio_transcription"] = (
                realtime_settings.input_audio_transcription
            )

        # Build model_config
        model_config = {
            "api_key": self._api_key,
            "initial_model_settings": initial_model_settings,
        }

        # Get the session from runner.run()
        # This returns a RealtimeSession that needs to be used as async context manager
        self._realtime_session = await self._runner.run(model_config=model_config)

        # Enter the async context manager
        await self._realtime_session.__aenter__()

        return self._realtime_session

    async def stream_events(self) -> AsyncIterator[RealtimeEventBase]:
        """Stream realtime events from the agent.

        Yields:
            RPC realtime event objects

        Raises:
            RuntimeError: If session not connected
        """
        if not self._realtime_session:
            raise RuntimeError("Session not connected. Call connect() first.")

        # Stream events from the realtime session (it's an async iterator)
        async for event in self._realtime_session:
            # Convert SDK event to RPC event
            rpc_event = map_realtime_session_event_to_rpc(event, self._run_id)

            if rpc_event:
                # Handle audio storage for audio events
                if rpc_event.type == "realtime_audio":
                    await self._store_audio_chunk(rpc_event)

                yield rpc_event

    async def _store_audio_chunk(self, audio_event) -> None:
        """Store an audio chunk to disk and save metadata.

        Args:
            audio_event: RealtimeAudioEvent with audio data
        """
        # Decode base64 audio
        audio_bytes = base64.b64decode(audio_event.audio_base64)

        # Determine audio format (from realtime settings)
        audio_format = self.spec.realtime_settings.output_audio_format

        # Save audio file
        relative_path = await self.audio_storage.save_audio_chunk(
            session_id=self.session_id,
            item_id=audio_event.item_id,
            content_index=audio_event.content_index,
            audio_data=audio_bytes,
            audio_format=audio_format,
        )

        # Save metadata to database
        history_db.save_audio_metadata(
            session_id=self.session_id,
            item_id=audio_event.item_id,
            content_index=audio_event.content_index,
            file_path=relative_path,
            audio_format=audio_format,
            duration_ms=audio_event.delta_ms,
            project_slug=self._project_slug,
            agent_slug=self._agent_slug,
        )

    async def send_audio(self, audio_base64: str, commit: bool = False) -> None:
        """Send audio input to the realtime agent.

        Args:
            audio_base64: Base64-encoded audio data
            commit: If True, commit the audio and trigger response generation

        Raises:
            RuntimeError: If session not connected
        """
        if not self._realtime_session:
            raise RuntimeError("Session not connected. Call connect() first.")

        # Decode base64 audio
        audio_bytes = base64.b64decode(audio_base64)

        # Send to realtime session
        # commit=False means streaming (buffer audio)
        # commit=True means commit and trigger response
        await self._realtime_session.send_audio(audio_bytes, commit=commit)

    async def send_text(self, text: str) -> None:
        """Send text input to the realtime agent.

        Args:
            text: Text message to send

        Raises:
            RuntimeError: If session not connected
        """
        if not self._realtime_session:
            raise RuntimeError("Session not connected. Call connect() first.")

        # Send message to session (accepts string or structured message)
        await self._realtime_session.send_message(text)

    async def interrupt(self) -> None:
        """Interrupt the current agent response.

        Raises:
            RuntimeError: If session not connected
        """
        if not self._realtime_session:
            raise RuntimeError("Session not connected. Call connect() first.")

        await self._realtime_session.interrupt()

    async def stop(self) -> None:
        """Stop the realtime session and clean up resources."""
        self._active = False

        # Cancel event streaming task if running
        if self._event_task and not self._event_task.done():
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass

        # Exit the realtime session's async context manager
        if self._realtime_session:
            try:
                await self._realtime_session.__aexit__(None, None, None)
            except Exception:
                pass
            self._realtime_session = None

        self._runner = None
        self._agent = None
        self._run_id = None

    @asynccontextmanager
    async def managed(self):
        """Context manager for automatic session lifecycle management.

        Example:
            async with session.managed():
                await session.connect()
                async for event in session.stream_events():
                    # Process events
                    pass
        """
        try:
            await self.start()
            yield self
        finally:
            await self.stop()
