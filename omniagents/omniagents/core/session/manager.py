from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from typing import Dict, Optional, Any

from omniagents.core.debug import Debug
from .history_db import (
    load_history,
    register_session,
    get_archived,
    set_archived,
    list_sessions as db_list_sessions,
    append_message as db_append_message,
    get_user_messages,
    get_session_metadata,
    update_session_metadata,
)

__all__ = ["Session", "SessionManager", "sm"]


class Session:  # noqa: D101
    """Lightweight container for a client session.

    A Session keeps track of the streaming task currently running for the
    client as well as its chat history so consecutive *start_run* calls
    can preserve context.
    """

    def __init__(
        self,
        project_slug: str | None,
        agent_slug: str | None,
        session_id: str | None = None,
    ) -> None:  # noqa: D401
        t_init = time.perf_counter()
        self._project_slug = project_slug
        self._agent_slug = agent_slug
        self.id: str = session_id or str(uuid.uuid4())
        self.user_id: str | None = None  # set by service after auth
        self.history: list[dict] = (
            load_history(self.id, project_slug=project_slug, agent_slug=agent_slug)
            if session_id
            else []
        )
        t_after_history = time.perf_counter()
        if session_id:
            self._repair_history_after_unclean_shutdown()
        t_after_repair = time.perf_counter()
        self.current_task: Optional[asyncio.Task] = None
        self.current_stream_result: Any | None = None
        self.cancel_requested: bool = False
        self.cancel_end_reason: str | None = None
        # Holds (method, payload) tuples that could not be delivered because
        # the WebSocket connection was already closed.  When the client
        # reconnects we replay everything that is still pending so the UI can
        # catch up (e.g. pending tool-approval requests).
        self.pending_events: list[tuple[str, dict]] = []
        # Active Rpc channels (one per concurrent client connection).  We
        # may have more than one in case the user opens multiple tabs.
        # Disconnected channels are automatically culled when a send fails.
        self.channels: list[Any] = []
        # Map active run_id -> preferred channel that initiated the run.
        self.run_channels: Dict[str, Any] = {}
        # Flag indicating whether this session was archived by the client.
        self.archived: bool = (
            get_archived(self.id, project_slug=project_slug, agent_slug=agent_slug)
            if session_id
            else False
        )
        self.always_allow_tools: set[str] = set()
        self.always_allow_patterns: set[str] = set()
        self.context: Any | None = None
        self.variables: Any | None = None
        self.active_run_id: str | None = None
        self.active_run_started_payload: dict | None = None
        self.active_run_status_payload: dict | None = None
        self.active_run_set_status_payload: dict | None = None
        self.active_session_state_payload: dict | None = None
        self.pending_client_requests: Dict[str, dict] = {}
        # Tool approval prompts awaiting user response (SDK 0.16+ flow).
        # Keyed by tool call_id; replayed to channels that connect mid-run.
        self.pending_tool_approvals: Dict[str, dict] = {}
        # Hosted-MCP approval prompts awaiting user response (SDK 0.16+).
        # Keyed by ``McpApprovalRequest.id``; replayed on reconnect like
        # ``pending_tool_approvals``.
        self.pending_mcp_approvals: Dict[str, dict] = {}
        # Model configuration for runtime switching
        self.active_model: str | None = None
        self.model_config: Dict[str, Any] | None = None
        self.reasoning_effort: str | None = None
        self._reasoning_explicitly_set: bool = False
        try:
            ctx, vars_data = get_session_metadata(
                self.id, project_slug=project_slug, agent_slug=agent_slug
            )
            if ctx is not None:
                self.context = ctx
            if vars_data is not None:
                self.variables = vars_data
        except Exception:
            self.context = None
            self.variables = None
        t_end = time.perf_counter()
        Debug.log(
            f"[profile] Session.__init__ session={self.id} "
            f"history_ms={(t_after_history - t_init) * 1000:.1f} "
            f"repair_ms={(t_after_repair - t_after_history) * 1000:.1f} "
            f"metadata_ms={(t_end - t_after_repair) * 1000:.1f} "
            f"total_ms={(t_end - t_init) * 1000:.1f} "
            f"history_items={len(self.history)}"
        )

    def append_message(self, message: dict) -> None:
        """Add a message to this session and persist it."""
        self.history.append(message)
        db_append_message(
            self.id,
            message,
            project_slug=self._project_slug,
            agent_slug=self._agent_slug,
        )

    # --- openai-agents SDK Session protocol ---
    # These async methods make Session compatible with agents.memory.Session,
    # so it can be passed directly to Runner.run(session=...).
    # They delegate to the existing sync persistence layer.

    @property
    def session_id(self) -> str:  # SDK Protocol field
        return self.id

    async def get_items(self, limit: int | None = None) -> list[dict]:
        """SDK Session protocol: retrieve conversation items.

        Returns the latest *limit* items in chronological order, or all items
        when *limit* is None.
        """
        items = list(self.history)
        if limit is not None and limit >= 0:
            items = items[-limit:]
        return items

    async def add_items(self, items: list[dict]) -> None:
        """SDK Session protocol: append items to the conversation."""
        for item in items:
            # Reuse the sync append_message so existing persistence + repair
            # logic stays the single source of truth.
            self.append_message(item)

    async def pop_item(self) -> dict | None:
        """SDK Session protocol: remove and return the most recent item.

        Note: persistence-layer pop is not currently exposed by history_db,
        so this only mutates in-memory history. Resumed sessions will see
        the popped item again. Suitable for SDK-internal rewinds within a
        single run.
        """
        if not self.history:
            return None
        return self.history.pop()

    async def clear_session(self) -> None:
        """SDK Session protocol: clear the in-memory conversation.

        Persistent rows are NOT deleted; use the CLI / archive flow if you
        need a hard wipe. This matches the existing OmniAgents semantics
        where archival is a metadata flag rather than a destructive op.
        """
        self.history.clear()

    def _repair_history_after_unclean_shutdown(self) -> None:
        response_call_ids_in_order: list[str] = []
        response_call_ids_seen: set[str] = set()
        response_outputs_seen: set[str] = set()

        chat_tool_call_ids_in_order: list[str] = []
        chat_tool_call_ids_seen: set[str] = set()
        chat_tool_outputs_seen: set[str] = set()

        for item in self.history:
            if not isinstance(item, dict):
                continue

            item_type = item.get("type")
            if isinstance(item_type, str) and item_type.endswith("_call_output"):
                call_id = item.get("call_id")
                if isinstance(call_id, str) and call_id:
                    response_outputs_seen.add(call_id)
                continue

            if (
                isinstance(item_type, str)
                and item_type.endswith("_call")
                and not item_type.endswith("_call_output")
            ):
                call_id = item.get("call_id")
                if (
                    isinstance(call_id, str)
                    and call_id
                    and call_id not in response_call_ids_seen
                ):
                    response_call_ids_seen.add(call_id)
                    response_call_ids_in_order.append(call_id)
                continue

            role = item.get("role")
            if role == "assistant":
                tool_calls = item.get("tool_calls")
                if isinstance(tool_calls, list):
                    for tool_call in tool_calls:
                        if not isinstance(tool_call, dict):
                            continue
                        tool_call_id = tool_call.get("id")
                        if (
                            isinstance(tool_call_id, str)
                            and tool_call_id
                            and tool_call_id not in chat_tool_call_ids_seen
                        ):
                            chat_tool_call_ids_seen.add(tool_call_id)
                            chat_tool_call_ids_in_order.append(tool_call_id)
                continue

            if role == "tool":
                tool_call_id = item.get("tool_call_id")
                if isinstance(tool_call_id, str) and tool_call_id:
                    chat_tool_outputs_seen.add(tool_call_id)
                continue

        missing_responses = [
            cid
            for cid in response_call_ids_in_order
            if cid not in response_outputs_seen
        ]
        for call_id in missing_responses:
            self.append_message(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": "[Missing tool output: previous process terminated]",
                }
            )

        missing_chat = [
            cid
            for cid in chat_tool_call_ids_in_order
            if cid not in chat_tool_outputs_seen
        ]
        for tool_call_id in missing_chat:
            self.append_message(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": "[Missing tool output: previous process terminated]",
                }
            )


class SessionManager:  # noqa: D101
    """Holds *all* live sessions for the server process."""

    def __init__(
        self, project_slug: str | None, agent_slug: str | None
    ) -> None:  # noqa: D401
        self._project_slug = project_slug
        self._agent_slug = agent_slug
        self._by_id: Dict[str, Session] = {}
        self._task_map: Dict[str, Session] = {}

    # ---------------------------------------------------------------------
    # Session lifecycle helpers
    # ---------------------------------------------------------------------
    def get_or_create(self, session_id: str | None = None) -> Session:  # noqa: D401
        if session_id and session_id in self._by_id:
            return self._by_id[session_id]
        sess = Session(self._project_slug, self._agent_slug, session_id)
        register_session(
            sess.id, project_slug=self._project_slug, agent_slug=self._agent_slug
        )
        self._by_id[sess.id] = sess
        return sess

    def fork_session(
        self,
        source_session: Session,
        *,
        new_session_id: str | None = None,
        history: list[dict] | None = None,
        context: Any | None = None,
        variables: Any | None = None,
    ) -> Session:
        new_session = self.get_or_create(new_session_id)
        new_history = list(
            history if history is not None else list(source_session.history)
        )
        new_session.history = []
        register_session(
            new_session.id, project_slug=self._project_slug, agent_slug=self._agent_slug
        )
        for msg in new_history:
            try:
                new_session.append_message(dict(msg))
            except Exception:
                continue
        new_session.context = context if context is not None else source_session.context
        new_session.variables = (
            variables if variables is not None else source_session.variables
        )
        try:
            self.persist_metadata(
                new_session,
                context=new_session.context,
                variables=new_session.variables,
            )
        except Exception:
            pass
        return new_session

    def bind_task(self, task: asyncio.Task, session: Session) -> None:  # noqa: D401
        run_id = task.get_name()
        self._task_map[run_id] = session
        session.current_task = task
        session.current_stream_result = None
        session.cancel_requested = False
        session.cancel_end_reason = None
        task.add_done_callback(lambda _: self._task_map.pop(run_id, None))

    def get_by_run_id(self, run_id: str) -> Optional[Session]:  # noqa: D401
        return self._task_map.get(run_id)

    async def cancel(self, run_id: str, *, timeout: float = 5.0) -> bool:  # noqa: D401
        sess = self._task_map.get(run_id)
        if not sess:
            return False
        task = sess.current_task
        if task is None:
            self._task_map.pop(run_id, None)
            return False
        if task.done():
            self._task_map.pop(run_id, None)
            sess.current_task = None
            sess.current_stream_result = None
            return False

        sess.cancel_requested = True
        if not sess.cancel_end_reason:
            sess.cancel_end_reason = "cancelled"

        stream = getattr(sess, "current_stream_result", None)
        force_cancel = False

        if stream and hasattr(stream, "cancel"):
            try:
                cancel_result = stream.cancel("immediate")
                if inspect.isawaitable(cancel_result):
                    await asyncio.wait_for(cancel_result, timeout)
            except Exception:
                force_cancel = True
        else:
            force_cancel = True

        if not force_cancel and not task.done():
            try:
                await asyncio.wait_for(task, timeout)
            except asyncio.TimeoutError:
                force_cancel = True
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        if force_cancel and not task.done():
            if not task.cancelled():
                task.cancel()
            try:
                await asyncio.wait_for(task, timeout)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        sess.current_task = None
        sess.current_stream_result = None
        self._task_map.pop(run_id, None)
        return True

    def archive_session(self, session_id: str) -> bool:  # noqa: D401
        """Mark *session_id* as archived."""
        sess = self._by_id.get(session_id)
        if not sess:
            return False
        sess.archived = True
        set_archived(
            session_id,
            True,
            project_slug=self._project_slug,
            agent_slug=self._agent_slug,
        )
        return True

    def list_sessions(self) -> list[dict]:  # noqa: D401
        """Return metadata for all known sessions."""
        return db_list_sessions(
            project_slug=self._project_slug, agent_slug=self._agent_slug
        )

    def persist_metadata(
        self, session: Session, *, context: Any, variables: Any
    ) -> None:
        update_session_metadata(
            session.id,
            context=context,
            variables=variables,
            project_slug=self._project_slug,
            agent_slug=self._agent_slug,
        )

    def get_user_messages(
        self,
        limit: int,
        include_archived: bool,
    ) -> list[dict]:  # noqa: D401
        return get_user_messages(
            limit=limit,
            include_archived=include_archived,
            project_slug=self._project_slug,
            agent_slug=self._agent_slug,
        )


sm = SessionManager("legacy", "legacy")
