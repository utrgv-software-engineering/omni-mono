"""Realtime agent service for JSON-RPC protocol.

This module provides the RealtimeService class for managing realtime agent
sessions over JSON-RPC WebSocket connections.
"""

import asyncio
import uuid
from typing import Optional, Dict, Any

from omniagents.rpc import RpcMethodsBase
from omniagents.rpc.realtime_session import RealtimeSession
from omniagents.core.agents.specs import AgentSpec


class RealtimeService(RpcMethodsBase):
    """JSON-RPC service for realtime agent operations.

    This service handles:
    - Starting and stopping realtime sessions
    - Forwarding audio/text input to the agent
    - Streaming realtime events back to the client
    - Session lifecycle management
    """

    def __init__(self, spec: AgentSpec, settings: Optional[Dict[str, Any]] = None):
        """Initialize the realtime service.

        Args:
            spec: Agent specification (must have realtime_mode=True)
            settings: Settings dict with API keys, etc.
        """
        super().__init__()

        if not spec.realtime_mode:
            raise ValueError(
                "RealtimeService requires AgentSpec with realtime_mode=True"
            )

        self.spec = spec
        self.settings = settings or {}

        # Active sessions: session_id -> RealtimeSession
        self._sessions: Dict[str, RealtimeSession] = {}

        # Event streaming tasks: session_id -> asyncio.Task
        self._event_tasks: Dict[str, asyncio.Task] = {}

    async def start_session(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Start a new realtime session.

        Args:
            session_id: Optional session ID (generated if not provided)

        Returns:
            Dict with session_id and run_id

        Raises:
            RuntimeError: If session already exists
        """
        if session_id is None:
            session_id = str(uuid.uuid4())

        if session_id in self._sessions:
            raise RuntimeError(f"Session {session_id} already exists")

        # Create session
        session = RealtimeSession(
            spec=self.spec,
            settings=self.settings,
            session_id=session_id,
        )

        # Start session
        run_id = await session.start()
        await session.connect()

        # Store session
        self._sessions[session_id] = session

        # Start event streaming task
        task = asyncio.create_task(self._stream_session_events(session_id))
        self._event_tasks[session_id] = task

        return {
            "session_id": session_id,
            "run_id": run_id,
        }

    async def _stream_session_events(self, session_id: str) -> None:
        """Background task to stream events from session to client.

        Args:
            session_id: The session ID
        """
        session = self._sessions.get(session_id)
        if not session:
            return

        try:
            async for event in session.stream_events():
                # Send event as notification to client
                if self.channel:
                    event_dict = event.to_dict()
                    await self.channel.async_call("realtime_event", event_dict)
        except asyncio.CancelledError:
            # Task was cancelled during cleanup
            pass
        except Exception as e:
            # Log error and notify client
            if self.channel:
                await self.channel.async_call(
                    "realtime_error",
                    {
                        "session_id": session_id,
                        "error": str(e),
                    },
                )

    async def send_audio(
        self, session_id: str, audio_base64: str, commit: bool = False
    ) -> bool:
        """Send audio input to a realtime session.

        Args:
            session_id: The session ID
            audio_base64: Base64-encoded audio data
            commit: If True, commit the audio and trigger response generation

        Returns:
            True on success

        Raises:
            ValueError: If session not found
        """
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        await session.send_audio(audio_base64, commit=commit)
        return True

    async def send_text(self, session_id: str, text: str) -> bool:
        """Send text input to a realtime session.

        Args:
            session_id: The session ID
            text: Text message to send

        Returns:
            True on success

        Raises:
            ValueError: If session not found
        """
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        await session.send_text(text)
        return True

    async def interrupt(self, session_id: str) -> bool:
        """Interrupt the current agent response.

        Args:
            session_id: The session ID

        Returns:
            True on success

        Raises:
            ValueError: If session not found
        """
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        await session.interrupt()
        return True

    async def stop_session(self, session_id: str) -> bool:
        """Stop a realtime session.

        Args:
            session_id: The session ID

        Returns:
            True on success

        Raises:
            ValueError: If session not found
        """
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        # Cancel event streaming task
        task = self._event_tasks.get(session_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Stop session
        await session.stop()

        # Remove from tracking
        del self._sessions[session_id]
        if session_id in self._event_tasks:
            del self._event_tasks[session_id]

        return True

    async def get_session_info(self, session_id: str) -> Dict[str, Any]:
        """Get information about a realtime session.

        Args:
            session_id: The session ID

        Returns:
            Dict with session information

        Raises:
            ValueError: If session not found
        """
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        return {
            "session_id": session.session_id,
            "run_id": session._run_id,
            "active": session._active,
        }

    async def list_sessions(self) -> Dict[str, Any]:
        """List all active realtime sessions.

        Returns:
            Dict with session information
        """
        return {
            "sessions": [
                {
                    "session_id": sid,
                    "run_id": session._run_id,
                    "active": session._active,
                }
                for sid, session in self._sessions.items()
            ]
        }

    async def cleanup(self) -> None:
        """Clean up all active sessions."""
        session_ids = list(self._sessions.keys())
        for session_id in session_ids:
            try:
                await self.stop_session(session_id)
            except Exception:
                pass
