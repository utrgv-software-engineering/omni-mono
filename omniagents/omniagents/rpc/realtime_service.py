"""Realtime agent service for JSON-RPC protocol.

This module provides the RealtimeService class for managing realtime agent
sessions over JSON-RPC WebSocket connections.
"""

import asyncio
import uuid
from typing import Optional, Dict, Any

from omniagents.rpc import RpcMethodsBase
from omniagents.rpc.auth import AuthorizationDenied, AuthorizationPolicy
from omniagents.rpc.realtime_session import (
    RealtimeSession,
    realtime_credential_status,
)
from omniagents.core.agents.specs import AgentSpec


class RealtimeService(RpcMethodsBase):
    """JSON-RPC service for realtime agent operations.

    This service handles:
    - Starting and stopping realtime sessions
    - Forwarding audio/text input to the agent
    - Streaming realtime events back to the client
    - Session lifecycle management
    """

    def __init__(
        self,
        spec: AgentSpec,
        settings: Optional[Dict[str, Any]] = None,
        settings_resolver: Optional[Any] = None,
    ):
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
        self.settings_resolver = settings_resolver

        # Active sessions: session_id -> RealtimeSession
        self._sessions: Dict[str, RealtimeSession] = {}

        # Event streaming tasks: session_id -> asyncio.Task
        self._event_tasks: Dict[str, asyncio.Task] = {}

        # Owner user_id per active session (None = unowned/anonymous)
        self._session_owners: Dict[str, Optional[str]] = {}
        # Authorization mode; ``build_app`` installs the deployment's policy.
        # Permissive by default so embedded/single-user use is unchanged.
        self.authorization_policy = AuthorizationPolicy(enforced=False)

    # ------------------------------------------------------------------
    #  Central RPC authorization (same contract as AgentService)
    # ------------------------------------------------------------------

    def _current_owner_user_id(self) -> Optional[str]:
        channel = self.channel
        principal = getattr(channel, "principal", None) if channel else None
        return principal.user_id if principal is not None else None

    async def authorize_rpc(self, principal, method: str, params: dict) -> None:
        """Deny access to realtime sessions owned by another user.

        Invoked by :class:`~omniagents.rpc.server.JsonRpcEndpoint` before
        dispatching every request on ``/ws/realtime``.
        """
        session_id = (params or {}).get("session_id")
        if not isinstance(session_id, str) or session_id not in self._sessions:
            return
        owner = self._session_owners.get(session_id)
        if self.authorization_policy.can_access(principal, owner):
            return
        raise AuthorizationDenied(
            "Not authorized to access this realtime session",
            resource="realtime_session",
            resource_id=session_id,
        )

    def _effective_settings(self) -> Dict[str, Any]:
        """Settings for a probe or session — the resolver's view when it works."""
        if callable(self.settings_resolver):
            try:
                resolved = self.settings_resolver()
            except Exception:
                resolved = None
            if resolved is not None:
                return resolved
        return self.settings

    async def capabilities(self) -> Dict[str, Any]:
        """Probe voice availability without opening a session (and thus without creating a trace).

        ``enabled`` reflects whether a credential is configured. Clients gate
        the mic button on it, so reporting True unconditionally puts up a
        control that fails the moment it is clicked. ``detail`` names the
        credential source when enabled, and the reason when not; it never
        contains the credential.

        This is an offline check by design — it never refreshes a token. A
        probe that ran on every client boot would otherwise spend refresh
        tokens, and stamp the store as broken on rejection, just for asking.
        """
        available, detail = realtime_credential_status(self._effective_settings())
        return {
            "enabled": available,
            "kind": "realtime",
            "agent_name": getattr(self.spec, "name", None),
            "detail": detail,
        }

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

        owner_user_id = self._current_owner_user_id()

        # Create session bound to the authenticated principal
        session = RealtimeSession(
            spec=self.spec,
            settings=self._effective_settings(),
            session_id=session_id,
            owner_user_id=owner_user_id,
        )

        # Start session
        run_id = await session.start()
        await session.connect()

        # Store session
        self._sessions[session_id] = session
        self._session_owners[session_id] = owner_user_id

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
                    try:
                        event_dict = event.to_dict()
                        await self.channel.async_call("realtime_event", event_dict)
                    except Exception:
                        # Client likely disconnected; stop streaming
                        break
        except asyncio.CancelledError:
            # Task was cancelled during cleanup
            pass
        except Exception as e:
            # Log error and notify client
            try:
                if self.channel:
                    await self.channel.async_call(
                        "realtime_error",
                        {
                            "session_id": session_id,
                            "error": str(e),
                        },
                    )
            except Exception:
                pass

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
        self._session_owners.pop(session_id, None)

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
        """List active realtime sessions visible to the calling principal.

        Returns:
            Dict with session information
        """
        channel = self.channel
        principal = getattr(channel, "principal", None) if channel else None
        return {
            "sessions": [
                {
                    "session_id": sid,
                    "run_id": session._run_id,
                    "active": session._active,
                }
                for sid, session in self._sessions.items()
                if self.authorization_policy.can_access(
                    principal, self._session_owners.get(sid)
                )
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
