from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from dataclasses import dataclass, field
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

__all__ = [
    "QueuedItem",
    "Session",
    "SessionManager",
    "sm",
    "filter_history_for_api",
    "strip_internal_keys_for_api",
]


@dataclass
class QueuedItem:
    """A message queued for serialized processing on a session.

    Two flavours:
    - ``trigger_run=False``: append to history, no run.
    - ``trigger_run=True``:  ``start_run`` with this content (which itself
      appends the prompt and drives a turn).

    Each item carries a stable ``id`` so clients can target it for
    cancellation/edit while it's still in the queue.
    """

    content: str
    role: str = "user"
    trigger_run: bool = False
    variables: Optional[Dict[str, Any]] = None
    safe_tool_overrides: Optional[Dict[str, Any]] = None
    source: Optional[str] = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    enqueued_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe wire shape for ``list_queue`` and ``queue_changed``."""
        return {
            "id": self.id,
            "content": self.content,
            "role": self.role,
            "trigger_run": self.trigger_run,
            "variables": self.variables,
            "safe_tool_overrides": self.safe_tool_overrides,
            "source": self.source,
            "enqueued_at": self.enqueued_at,
        }


# ---------------------------------------------------------------------
# Helpers shared between the SDK Session protocol (Session.get_items)
# and the JSON-RPC ``get_session_history`` method.  These run when
# raw history is about to be handed to the OpenAI Responses API: items
# are reordered so reasoning items keep their required pairings, and
# output-only metadata that the API rejects on input is stripped.
# ---------------------------------------------------------------------


def filter_history_for_api(history: list[dict]) -> list[dict]:
    """Reorder history items to match OpenAI API requirements.

    During streaming, items arrive in this order:
      reasoning → function_call(s) → message → function_call_output(s)

    But OpenAI expects:
      reasoning → message → function_call(s) → function_call_output(s)

    This reordering ensures:
    - reasoning items are immediately followed by messages (not function_calls)
    - parallel function_calls are grouped together after the message
    - function_call items are properly paired with their outputs

    This prevents errors like:
    "Item 'rs_xxx' of type 'reasoning' was provided without its required following item."
    "No tool call found for function call output with call_id xxx"
    """
    reordered: list[dict] = []
    i = 0
    consumed_indices: set[int] = set()
    while i < len(history):
        if i in consumed_indices:
            i += 1
            continue
        item = history[i]
        item_type = item.get("type", "message")

        # Handle reasoning items - they need special treatment for parallel calls
        if item_type == "reasoning":
            reasoning = item
            j = i + 1

            # Collect all parallel function calls that follow the reasoning.
            # Calls and outputs may be interleaved (fc → fc_output → fc → fc_output)
            # or grouped (fc → fc → fc_output → fc_output).  Handle both by
            # skipping over outputs while scanning for calls.
            function_calls: list[dict] = []
            interleaved_outputs: list[tuple[int, dict]] = []
            while j < len(history):
                next_item = history[j]
                next_type = next_item.get("type", "")
                if (
                    isinstance(next_type, str)
                    and next_type.endswith("_call")
                    and not next_type.endswith("_call_output")
                ):
                    function_calls.append(next_item)
                    j += 1
                elif isinstance(next_type, str) and next_type.endswith("_call_output"):
                    # Output interleaved between calls – collect and skip
                    interleaved_outputs.append((j, next_item))
                    j += 1
                else:
                    break

            # A second reasoning may sit between the last output and the
            # message (the model emitted reasoning before the preamble text).
            # Skip over it so we can still find the paired message.
            trailing_reasoning = None
            if (
                j < len(history)
                and history[j].get("type") == "reasoning"
                and j + 1 < len(history)
                and history[j + 1].get("type") == "message"
            ):
                trailing_reasoning = history[j]
                j += 1

            # Find the message that pairs with this reasoning
            message = None
            if j < len(history) and history[j].get("type") == "message":
                message = history[j]
                j += 1

            # Items may be persisted in message-before-calls order
            # (reasoning → message → function_call) instead of streaming order
            # (reasoning → function_call → message).  Scan for calls after the
            # message so both orderings are handled.
            if message and not function_calls:
                while j < len(history):
                    next_item = history[j]
                    next_type = next_item.get("type", "")
                    if (
                        isinstance(next_type, str)
                        and next_type.endswith("_call")
                        and not next_type.endswith("_call_output")
                    ):
                        function_calls.append(next_item)
                        j += 1
                    elif isinstance(next_type, str) and next_type.endswith(
                        "_call_output"
                    ):
                        interleaved_outputs.append((j, next_item))
                        j += 1
                    else:
                        break

            # Collect outputs for the function calls.
            # Start from interleaved outputs already gathered, then scan
            # forward for any remaining (the grouped layout).
            outputs: list[dict] = []
            call_ids = {fc.get("call_id") for fc in function_calls if fc.get("call_id")}

            unmatched_interleaved: list[dict] = []
            for idx, out_item in interleaved_outputs:
                out_cid = out_item.get("call_id")
                if out_cid in call_ids:
                    outputs.append(out_item)
                    consumed_indices.add(idx)
                    call_ids.discard(out_cid)
                else:
                    # Output doesn't belong to any call in this group —
                    # preserve it so it isn't silently dropped.
                    unmatched_interleaved.append(out_item)

            if call_ids:
                for k in range(j, len(history)):
                    next_item = history[k]
                    next_type = next_item.get("type", "")
                    if not (
                        isinstance(next_type, str)
                        and next_type.endswith("_call_output")
                    ):
                        continue
                    output_call_id = next_item.get("call_id")
                    if output_call_id in call_ids:
                        outputs.append(next_item)
                        consumed_indices.add(k)
                        call_ids.remove(output_call_id)
                        if not call_ids:
                            break

            # Emit any interleaved outputs that didn't match a call in this
            # group before the group itself, so they aren't silently lost.
            reordered.extend(unmatched_interleaved)

            if message:
                # Valid reasoning group: reorder to reasoning → message → function_calls → outputs
                reordered.append(reasoning)
                reordered.append(message)
                reordered.extend(function_calls)
                reordered.extend(outputs)
            elif function_calls:
                # Orphaned reasoning (no message): drop reasoning but preserve tool calls
                # Azure rejects reasoning without messages AND rejects synthetic messages,
                # but accepts function_calls without reasoning if they have new IDs.
                # Create synthetic function_calls with new IDs to preserve the tool call history.
                for fc in function_calls:
                    # Find corresponding output
                    fc_output = None
                    for output in outputs:
                        if output.get("call_id") == fc.get("call_id"):
                            fc_output = output
                            break

                    # Create synthetic IDs (not referencing dropped reasoning)
                    original_id = fc.get("id", "unknown")
                    synth_call_id = f"call_synth_{original_id[3:] if original_id.startswith('fc_') else original_id}"
                    synth_fc_id = f"fc_synth_{original_id[3:] if original_id.startswith('fc_') else original_id}"

                    # Synthetic function call
                    synth_fc = {
                        "type": "function_call",
                        "id": synth_fc_id,
                        "call_id": synth_call_id,
                        "name": fc.get("name"),
                        "arguments": fc.get("arguments"),
                        "status": fc.get("status", "completed"),
                    }
                    reordered.append(synth_fc)

                    # Synthetic output
                    if fc_output:
                        synth_output = {
                            "type": "function_call_output",
                            "call_id": synth_call_id,
                            "output": fc_output.get("output"),
                        }
                        reordered.append(synth_output)
            # else: Orphaned reasoning with no function calls - just drop it

            i = j  # Skip past all processed items
        # Check if this is a function_call NOT preceded by reasoning
        elif (
            isinstance(item_type, str)
            and item_type.endswith("_call")
            and not item_type.endswith("_call_output")
        ):
            # Only process if we didn't already handle it as part of a reasoning group
            # Check if previous item was reasoning (would have been handled above)
            if i > 0 and reordered and reordered[-1].get("type") != "reasoning":
                call_id = item.get("call_id")
                function_call = item

                # Look ahead for the message and output
                message = None
                output = None
                j = i + 1

                # Find the message that comes after this call
                if j < len(history) and history[j].get("type") == "message":
                    message = history[j]
                    j += 1

                # Find the output that matches this call_id
                if call_id:
                    for k in range(j, len(history)):
                        next_item = history[k]
                        if (
                            isinstance(next_item.get("type", ""), str)
                            and next_item.get("type", "").endswith("_call_output")
                            and next_item.get("call_id") == call_id
                        ):
                            output = next_item
                            consumed_indices.add(k)
                            break

                # Reorder: message → function_call → output
                if message:
                    reordered.append(message)
                reordered.append(function_call)
                if output:
                    reordered.append(output)

                i = j  # Skip past the items we just processed
            else:
                # Already handled as part of reasoning group, skip
                i += 1
        else:
            # Not a reasoning or function_call, keep as-is
            reordered.append(item)
            i += 1

    return reordered


_STATUS_STRIP_TYPES = frozenset(
    {
        "reasoning",
        "message",
        "function_call",
        "function_call_output",
    }
)


def strip_internal_keys_for_api(items: list[dict]) -> list[dict]:
    """Drop OmniAgents-internal keys and output-only metadata before sending to the API.

    Strips:
    - The ``omniagents`` namespace dict (private bookkeeping)
    - ``phase`` (gpt-5.3-codex+ output marker rejected by Azure/litellm)
    - ``status`` on output-only types (``reasoning``, ``message``, ``function_call``,
      ``function_call_output``) which the API rejects on input
    - The ``id`` field on assistant ``message`` items whose paired ``reasoning`` was
      lost (older history saved before the reasoning-strip fix); without an id
      the API treats it as a plain assistant turn.

    Also ensures reasoning items have a ``summary`` list (the API requires at
    least an empty list).
    """
    # Build set of reasoning IDs present so we can detect orphaned messages
    reasoning_ids: set[str] = set()
    for item in items or []:
        if isinstance(item, dict) and item.get("type") == "reasoning":
            rid = item.get("id")
            if rid:
                reasoning_ids.add(rid)

    cleaned: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        copied = dict(item)
        copied.pop("omniagents", None)
        # The API returns `phase` on assistant messages (gpt-5.3-codex+),
        # but non-OpenAI providers (Azure, litellm) reject it as unknown.
        # Safe to strip unconditionally — the SDK re-adds it when needed.
        copied.pop("phase", None)
        # The API returns `status` on output items (reasoning, message, etc.)
        # but may reject it when those items are sent back as input.
        # Strip it from types where it's output-only metadata.
        if copied.get("type") in _STATUS_STRIP_TYPES:
            copied.pop("status", None)
        # Keep reasoning items (the API requires them before their paired
        # message).  Preserve encrypted_content when present — the API
        # requires it to associate reasoning with subsequent function_calls.
        # Only strip status which is output-only metadata.
        if copied.get("type") == "reasoning":
            copied.pop("status", None)
            # Ensure summary exists (API needs at least an empty list)
            if "summary" not in copied:
                copied["summary"] = []
        # Strip id from orphaned message items whose reasoning was lost.
        # This happens when history was saved before the reasoning-strip
        # fix: the reasoning item was dropped but the message kept its id,
        # so the API rejects it with "message without required reasoning".
        # Removing the id makes the API treat it as a plain assistant turn.
        if copied.get("type") == "message":
            msg_id = copied.get("id", "")
            if isinstance(msg_id, str) and msg_id.startswith("msg_"):
                expected_reasoning_id = "rs_" + msg_id[4:]
                if expected_reasoning_id not in reasoning_ids:
                    copied.pop("id", None)
        cleaned.append(copied)
    return cleaned


def _apply_legacy_compaction_window(history: list[dict]) -> list[dict]:
    """Trim history to items after the most recent legacy summary marker.

    Honors the ``"kind": "context_summary"`` marker on assistant messages
    that older sessions persisted before SDK-native compaction took over.
    Newly created sessions never write this marker, but the helper keeps
    pre-existing data usable.  When no marker is present this is an
    identity transform.
    """
    if not history:
        return []
    last_summary_idx: int | None = None
    for idx in range(len(history) - 1, -1, -1):
        item = history[idx]
        if (
            isinstance(item, dict)
            and item.get("role") == "assistant"
            and isinstance(item.get("omniagents"), dict)
            and item["omniagents"].get("kind") == "context_summary"
        ):
            last_summary_idx = idx
            break
    if last_summary_idx is None:
        return list(history)
    # Include the summary itself so the model still sees it.
    return list(history[last_summary_idx:])


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
        # Background-injected messages awaiting serialized processing.
        # Drained by AgentService._drain_queue, which serializes against
        # ``current_task`` so queued runs never collide with user-initiated ones.
        # Backed by a list (not asyncio.Queue) so clients can list/cancel
        # items by id via ``list_queue`` / ``cancel_queued_message``.
        self.message_queue: list[QueuedItem] = []
        self.message_queue_max: int = 100
        self.drainer_task: Optional[asyncio.Task] = None
        # Background-notification batching. Sources that produce
        # automated notifications (bash jobs exiting, workers
        # completing, webhooks) call AgentService.enqueue_notification
        # instead of enqueue_message. Those notifications buffer here
        # and are coalesced into ONE combined QueuedItem (trigger_run=
        # True, role="assistant") by _notification_flusher at the next
        # safe boundary: active run ends, short debounce when idle, or
        # immediately when a real user message is enqueued.
        self.pending_notifications: list[dict] = []
        self.notification_signal: asyncio.Event = asyncio.Event()
        self.notification_flush_now: asyncio.Event = asyncio.Event()
        self.notification_flusher_task: Optional[asyncio.Task] = None
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

        The OpenAI Responses API requires a specific item ordering and rejects
        certain output-only fields when items are sent back as input.  Apply
        the same reorder/strip helpers the bridge used to apply manually so
        the SDK's ``prepare_input_with_session`` receives clean history.
        """
        items = list(self.history)
        # Honor the legacy ``context_summary`` marker (a no-op for newly
        # created sessions which never write one).
        items = _apply_legacy_compaction_window(items)
        # Reorder so reasoning/message/function_call/output groups are valid
        # for the API and strip output-only metadata.
        items = strip_internal_keys_for_api(filter_history_for_api(items))
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
