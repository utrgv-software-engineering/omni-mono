from __future__ import annotations

import os
import asyncio
import logging
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from typing import AsyncGenerator, Optional, Any

from agents import RunConfig, ItemHelpers
from agents.run_state import RunState
from openai.types.responses import ResponseCompletedEvent
from omniagents.core.agents import mcp_ui
from omniagents.core.agents.approval import session_allows_mcp_tool, session_allows_tool
from omniagents.core.agents.mcp_identity import lookup_identity
from omniagents.core.debug import Debug
from omniagents.core.session.event_log import (
    channel_accepts_notification,
    stamp_session_event,
)
from omniagents.core.tools.artifacts import (
    reset_artifact_callback,
    set_artifact_callback,
)

# Setup logging
logger = logging.getLogger("omniagents.runtime")

# Configure file logging if requested (same as service.py)
LOG_FILE = os.getenv("OMNIAGENTS_SERVER_LOG_FILE")
if LOG_FILE:
    from pathlib import Path

    logger.setLevel(logging.DEBUG)
    log_path = Path(os.path.expanduser(LOG_FILE))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path, mode="a")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("[%(asctime)s] [runtime] %(levelname)s: %(message)s")
    )
    logger.addHandler(file_handler)

# ---------------------------------------------------------------------
# Helper: best-effort notify that swallows disconnect errors so the
#         streaming task can keep running even when the WebSocket is
#         already closed.  Returns True if the payload was *consumed* —
#         put on the wire, or refused by the connection's notification
#         policy — and False only when the socket is gone.
#
#         This mirrors ``AgentService._safe_notify`` and shares its
#         predicate rather than re-deriving the policy, because both feed
#         the same session-scoped ``pending_events`` queue and a drift
#         between them would be invisible (tkt_m3nx8D3Rfngw).
# ---------------------------------------------------------------------


async def _safe_notify(channel, method: str, payload: dict) -> bool:  # noqa: D401
    """Try to send *payload* via *channel*; return False if socket is gone."""
    if not channel_accepts_notification(channel, method):
        return True
    try:
        await channel.async_call(method, args=payload)
        return True
    except Exception:
        # Most commonly RuntimeError or WebSocketDisconnect from Starlette
        return False


# ---------------------------------------------------------------------
# Broadcast helper: send to every live channel attached to the Session.
# Returns True if at least one channel received the message.
# ---------------------------------------------------------------------


async def _broadcast(session, method: str, payload: dict) -> bool:  # noqa: D401
    # Stamp + journal replayable events BEFORE the connectivity check so
    # events emitted while every client is disconnected are still
    # sequenced and resumable via ``resume_session``.
    payload = stamp_session_event(session, method, payload)

    if not session.channels:
        return False

    if method == "client_request":
        func = (payload or {}).get("function") if isinstance(payload, dict) else None
        if func == "ui.set_status":
            session.active_run_set_status_payload = dict(payload)

    delivered_any = False
    dead_channels = []
    for ch in list(session.channels):
        ok = await _safe_notify(ch, method, payload)
        if ok:
            delivered_any = True
        else:
            dead_channels.append(ch)

    # Remove channels that are no longer alive. Dropping the channel's
    # replay cursor as well keeps a dead consumer from pinning the
    # minimum-cursor floor and permanently stalling ack-driven journal
    # compaction (mirrors the cull in AgentService._broadcast_session).
    for ch in dead_channels:
        try:
            session.channels.remove(ch)
        except ValueError:
            pass
        event_log = getattr(session, "event_log", None)
        if event_log is not None:
            event_log.drop_consumer(ch)

    return delivered_any


# Event mapping helper ----------------------------------------------------


def _mcp_label_fields(session, public_name) -> dict:
    """Structured (server, tool) identity fields for MCP-derived tools.

    ``tool`` stays the flat wire name (the call-correlation key); these
    extra fields let clients display the original tool name and server
    without parsing the ``mcp_<server>__`` prefix.
    """
    if session is None or not isinstance(public_name, str):
        return {}
    identity = lookup_identity(session, public_name)
    if identity is None:
        return {}
    return {"server_label": identity[0], "tool_label": identity[1]}


def _install_run_diff_observer(service, session):
    """Bind this run's :class:`RunDiffObserver`. Returns ``(token, observer)``.

    Every observed mutation persists the run's aggregated diff as a
    ``run_diff`` item and pushes ``item_updated``, so a client sees the
    complete, current picture of what the run has changed after each edit —
    without waiting for ``run_end`` and without stitching together the
    per-edit diffs on the ``tool_result`` events.

    ``None`` when there is no run to attribute the edits to (rather than
    accumulating into an item with no turn).

    Callers should use :func:`run_diff_observer_scope`, which owns the
    teardown ORDER; this is its one implementation detail.
    """
    import asyncio as _asyncio

    task = _asyncio.current_task()
    run_id = task.get_name() if task is not None else None
    if not run_id:
        return None

    from omniagents.core.conversation.recorder import recorder_for_session
    from omniagents.core.conversation.run_diff import RunDiffObserver
    from omniagents.workspace import set_mutation_observer as _set_mutation_observer

    async def _publish(snapshot: dict) -> None:
        try:
            recorder = recorder_for_session(session)
            if recorder is None:
                return
            recorder.ensure_thread(
                session.id,
                user_id=getattr(session, "user_id", None),
                archived=bool(getattr(session, "archived", False)),
            )
            item = recorder.record_run_diff(
                session.id, run_id=run_id, snapshot=snapshot
            )
            if item is None:
                return
            await service.notify_item_updated(session, item)
        except Exception:
            logger.exception(
                "run diff publication failed",
                extra={"session": getattr(session, "id", None), "run": run_id},
            )

    observer = RunDiffObserver(_publish)
    try:
        return _set_mutation_observer(observer), observer
    except Exception:
        logger.exception("run diff observer could not be installed")
        return None


@asynccontextmanager
async def run_diff_observer_scope(service, session):
    """The run-diff observer's whole lifecycle: bind, reconcile, unbind.

    The teardown ORDER is a contract, not a detail:

    1. ``finalize()`` re-reads every tracked path — it MUST run while the
       run's workspace is still installed, or it reads the wrong tree;
    2. only then is the contextvar restored.

    That sequence used to be hand-written at each call site, which is
    exactly how the test harness drifted from ``stream_agent`` when
    :func:`_install_run_diff_observer` changed shape — and it surfaced only
    because the new return type happened to raise. A silent divergence
    would have passed. One context manager, used by every caller, makes the
    contract impossible to duplicate wrongly.

    Yields the observer (``None`` when none was installed) so a caller can
    inspect it; nothing is required of the caller but to leave the block.
    """
    installed = _install_run_diff_observer(service, session)
    if installed is None:
        yield None
        return
    token, observer = installed
    try:
        yield observer
    finally:
        # ``pending`` is the exception this teardown is unwinding, if any.
        # Captured BEFORE the awaits below, because inside an ``except``
        # block ``sys.exc_info()`` reports the newly-caught exception.
        pending = sys.exc_info()[1]
        try:
            await observer.finalize()
        except BaseException as exc:
            # Deliberately BaseException, not Exception. ``finalize()``
            # awaits once per tracked path, so a ``task.cancel()`` landing
            # in that window raises CancelledError — which ``except
            # Exception`` does not catch. It would then ESCAPE this
            # ``finally`` and REPLACE the run's own exception, so a run that
            # died with a RuntimeError would be recorded
            # ``end_reason="cancelled"`` with ``run_end.error`` dropped. The
            # window is proportional to the number of files the run touched.
            logger.exception("run diff finalization failed")
            if pending is None and not isinstance(exc, Exception):
                # Nothing was in flight, so swallowing a CancelledError
                # here would LOSE a genuine cancellation instead of masking
                # one. Ordinary exceptions still never escape: diff
                # bookkeeping must not fail a run.
                #
                # Known, deliberate: this path skips the unbind below.
                # Harmless today — the run task's context dies with it, and
                # contextvar tokens restore correctly when nested — but a
                # ``try/finally`` around the two steps would make the unbind
                # unconditional. Recorded as a follow-up rather than done
                # here, because restructuring the teardown is exactly the
                # kind of change that wants its own review.
                raise
        try:
            from omniagents.workspace import (
                reset_mutation_observer as _reset_mutation_observer,
            )

            _reset_mutation_observer(token)
        except BaseException as exc:
            logger.exception("run diff observer could not be unbound")
            if pending is None and not isinstance(exc, Exception):
                raise


def _stream_event_payload(item, run_id: str, *, session_id: str, session=None):
    """Map agent stream *item* into a notification name + payload.

    Returns (method_name, payload) or (None, None) if the item shouldn't
    be forwarded to the client.
    """
    if item.type == "tool_call_item":
        inp = item.to_input_item() or {}
        name = inp.get("name", "<tool>")
        call_id = inp.get("call_id")
        # Memoize so the matching ``tool_call_output_item`` (which has no
        # ``.name`` attribute on the SDK side) can recover the tool name
        # for the MCP-Apps UI side-channel lookup below.
        if isinstance(call_id, str) and isinstance(name, str):
            mcp_ui.remember_call(call_id, name)
        return "tool_called", {
            "run_id": run_id,
            "session_id": session_id,
            "tool": name,
            "input": inp.get("arguments", ""),
            **({"call_id": call_id} if call_id else {}),
            **_mcp_label_fields(session, name),
        }
    elif item.type == "tool_call_output_item":
        # Extract call_id if present on the underlying raw_item / input item
        call_id = None
        try:
            out_dict = item.to_input_item() if hasattr(item, "to_input_item") else {}
            if isinstance(out_dict, dict):
                call_id = out_dict.get("call_id")
        except Exception:
            pass

        # Extract metadata from RichToolOutput if present
        output = getattr(item, "output", "")
        ui_metadata: dict = {}

        # Check if output is a RichToolOutput instance
        from omniagents.core.tools import RichToolOutput

        if isinstance(output, RichToolOutput):
            ui_metadata = dict(output.ui_metadata)
            # String conversion will use __str__ which returns only llm_output

        # The SDK's ``ToolCallOutputItem`` exposes ``call_id`` but no
        # ``name`` — recover the tool name from the ``call_id`` map we
        # populated on the preceding ``tool_call_item`` event.
        tool_name = getattr(item, "name", None)
        if not tool_name and isinstance(call_id, str):
            tool_name = mcp_ui.lookup_tool_name(call_id)
        if not tool_name:
            tool_name = "<tool>"

        # MCP Apps: if the underlying MCP server returned a UI resource we
        # stashed via ``mcp_ui.record`` (see ``core/agents/mcp.py``), attach
        # it here so the host can render the inline UI alongside the
        # tool result. ``mcp_ui`` records under the *original* MCP tool
        # name (the call_tool wrapper sees originals), while the stream
        # carries the prefixed public name — translate via the identity
        # map before the lookup.
        label_fields = _mcp_label_fields(session, tool_name)
        mcp_ui_payload = mcp_ui.pop(label_fields.get("tool_label", tool_name))
        if mcp_ui_payload is not None:
            ui_metadata["display_type"] = "mcp_ui"
            ui_metadata["mcp_ui"] = mcp_ui_payload

        payload = {
            "run_id": run_id,
            "session_id": session_id,
            "tool": tool_name,
            "output": str(output),  # This calls __str__ for RichToolOutput
            **({"call_id": call_id} if call_id else {}),
            **label_fields,
        }

        # Only include metadata if it's not empty
        if ui_metadata:
            payload["metadata"] = ui_metadata

        return "tool_result", payload
    elif item.type == "message_output_item":
        text = ItemHelpers.text_message_output(item)
        # The model's own message id, when the provider minted one. Clients
        # use it to correlate this event with the canonical conversation
        # item without inventing an ordinal the server would have to agree
        # with (see protocol.md § Conversation Domain Objects).
        message_id = getattr(getattr(item, "raw_item", None), "id", None)
        payload = {
            "run_id": run_id,
            "session_id": session_id,
            "content": text,
            "is_final": False,
        }
        if isinstance(message_id, str) and message_id:
            payload["message_id"] = message_id
        return "message_output", payload
    return None, None


def _strip_phase_from_model_input(data):
    """RunConfig.call_model_input_filter that strips ``phase`` from all input items.

    The OpenAI Responses API returns ``phase`` on assistant messages (used by
    gpt-5.3-codex+).  When the agents SDK re-serialises output items for the
    next model turn (tool-call loop), ``phase`` leaks into the request body.
    Non-OpenAI providers (Azure via litellm proxy) reject it as an unknown
    parameter.  Stripping here catches both session-history replay *and*
    intra-run tool-call loops.
    """
    cleaned: list = []
    for item in data.model_data.input:
        if isinstance(item, dict) and "phase" in item:
            item = {k: v for k, v in item.items() if k != "phase"}
        cleaned.append(item)
    data.model_data.input = cleaned
    return data.model_data


async def stream_agent(
    agent,
    prompt: str,
    session,
    rpc_channel,
    *,
    service: Any | None = None,
    max_turns: Optional[int] = 20,
    context: Optional[Any] = None,
    max_input_tokens: Optional[int] = None,
    max_output_tokens: Optional[int] = None,
    include_user_message: bool = True,
    context_error_retry: bool = False,
    workflow_name: Optional[str] = None,
    group_id: Optional[str] = None,
    user_content: Optional[Any] = None,
    model_provider: Optional[Any] = None,
    model_label: Optional[str] = None,
    nest_handoff_history: bool = False,
    tool_execution_config: Optional[Any] = None,
    error_handlers: Optional[dict] = None,
    run_hooks: Optional[Any] = None,
    sdk_session: Optional[Any] = None,
    sandbox_run_config: Optional[Any] = None,
) -> dict | None:  # noqa: D401
    """Drive agent.run_streamed and forward events to *rpc_channel*.

    ``rpc_channel`` is the :class:`RpcMethodsBase` instance attached to the
    WebSocket connection and exposes ``.async_call(method, args)`` for
    server-to-client notifications.
    """

    # Publish the run's requested cwd (the agent's navigable working dir) and,
    # for HOST runs, install the HostWorkspace rooted there so
    # omniagents.workspace.* resolves relative to it. Sandbox runs do NOT get
    # a HostWorkspace here: their cwd is a *container* path, and
    # WorkspaceBindingCapability.bind installs the SandboxWorkspace (reading
    # the same requested cwd via the contextvar, clamped to the manifest
    # root). Building a HostWorkspace from a container path would mkdir it on
    # the host — exactly the leak we're avoiding.
    workspace_token = None
    try:
        ws_root: Optional[str] = None
        if isinstance(context, dict):
            v = context.get("workspace_root")
            if isinstance(v, str) and v.strip():
                ws_root = v
        else:
            v = getattr(context, "workspace_root", None)
            if isinstance(v, str) and v.strip():
                ws_root = v

        from omniagents.workspace import set_requested_cwd as _set_requested_cwd

        _set_requested_cwd(ws_root)

        is_sandbox_run = sandbox_run_config is not None
        if ws_root and not is_sandbox_run:
            from omniagents.workspace import HostWorkspace as _HostWorkspace
            from omniagents.workspace import current_workspace as _current_workspace
            from omniagents.workspace import set_workspace as _set_workspace

            active_workspace = _current_workspace()
            host_confine = getattr(active_workspace, "confine", True)
            workspace_token = _set_workspace(
                _HostWorkspace(root=ws_root, confine=host_confine)
            )
    except Exception:
        # Workspace setup is best-effort; fall back to default lazy
        # HostWorkspace(cwd) if anything goes wrong here.
        workspace_token = None

    # Aggregate this run's file edits into a run-level unified diff
    # (tkt_FqUypyxF2tzC). Bound AFTER the workspace so the observer reads
    # content back through whichever workspace this run actually uses, and
    # per-run so two concurrent runs never accumulate into one another.
    # The observer's bind/reconcile/unbind order lives in ONE place
    # (``run_diff_observer_scope``); an exit stack lets this long function
    # keep its existing shape while still closing the scope at the right
    # moment — before the workspace contextvar is restored.
    run_diff_scope = AsyncExitStack()
    if service is not None and session is not None:
        await run_diff_scope.enter_async_context(
            run_diff_observer_scope(service, session)
        )

    token = None
    if service is not None and session is not None:
        loop = asyncio.get_running_loop()

        async def _send_artifact(payload: dict, *, run_id: str | None) -> None:
            try:
                await asyncio.wait_for(
                    service.request_client_function(
                        "ui.add_artifact",
                        payload,
                        session=session,
                        run_id=run_id,
                        expect_result=False,
                    ),
                    timeout=2.0,
                )
            except Exception:
                pass

        def _artifact_callback(
            title: str,
            content: str,
            mode: str = "markdown",
            artifact_id: str | None = None,
        ) -> None:
            # Determine run_id if we're inside an asyncio task.  This will
            # fail with RuntimeError when called from a worker thread (sync
            # tools run via asyncio.to_thread), so default to None.
            run_id = None
            try:
                task = asyncio.current_task()
                if task is not None:
                    run_id = task.get_name()
            except RuntimeError:
                pass

            try:
                # Use run_coroutine_threadsafe because the callback may be
                # invoked from a worker thread (sync tools run via
                # asyncio.to_thread in the SDK's function_tool wrapper).
                asyncio.run_coroutine_threadsafe(
                    _send_artifact(
                        {
                            "title": title,
                            "content": content,
                            "mode": mode,
                            "artifact_id": artifact_id,
                        },
                        run_id=run_id,
                    ),
                    loop,
                )
            except Exception:
                pass

        token = set_artifact_callback(_artifact_callback)

    # ------------------------------------------------------------------
    # Build the new-turn input.  History is read from the session by the
    # SDK via ``Session.get_items()`` (which applies our reorder/strip
    # helpers), so we pass only the fresh user turn here.  When resuming
    # after an approval (``include_user_message=False``) the SDK pulls
    # everything it needs from session history.
    # ------------------------------------------------------------------
    user_msg: dict | None = None
    if include_user_message:
        if user_content is not None:
            user_msg = {"role": "user", "content": user_content}
        else:
            user_msg = {"role": "user", "content": prompt}
        agent_input: Any = [user_msg]
    else:
        agent_input = []

    Debug.log(
        f"[srv] ▶ start_run  session={session.id[:8]} prompt={prompt[:80]!r} (hist={len(session.history)})"
    )

    # Log the input structure for debugging
    history_from_db = len(session.history)
    items_this_turn = len(agent_input) - history_from_db
    logger.debug(
        f"API request starting - Session: {session.id}, "
        f"Total items: {len(agent_input)}, "
        f"From DB: {history_from_db}, "
        f"Added this turn: {items_this_turn}, "
        f"Include user message: {include_user_message}"
    )

    # Log item sequence (all fields, truncate content for readability)
    if logger.isEnabledFor(logging.DEBUG):
        import json

        item_sequence = []
        for idx, item in enumerate(agent_input):
            # Truncate content fields to keep log readable
            item_copy = dict(item)
            if "content" in item_copy and isinstance(item_copy["content"], str):
                if len(item_copy["content"]) > 100:
                    item_copy["content"] = (
                        item_copy["content"][:100] + "... (truncated)"
                    )

            # Show all fields as JSON
            item_json = json.dumps(item_copy, default=str)
            item_sequence.append(f"  [{idx}]: {item_json}")

        logger.debug(f"Item sequence:\n" + "\n".join(item_sequence))

    # Track pending tool calls that need results
    pending_tool_calls = {}
    # Buffer of items streamed in the *current* SDK run iteration.  The SDK
    # persists items to the session via ``save_result_to_session`` at turn
    # boundaries; if the run is cancelled mid-turn (or otherwise raises
    # before that boundary fires), those items aren't on disk yet.  We hold
    # them here so the ``finally`` block can flush them onto the session
    # before injecting cancellation outputs for any pending tool calls.
    # Seed with the user message — the SDK persists it at the top of
    # ``run_single_turn_streamed`` (well before any tool call streams), so
    # by the time we reach a cancellation path it's already on disk.  The
    # finally-block dedupe still catches early-cancellation cases where
    # the SDK never got that far.
    streamed_items_buffer: list[dict] = []
    if user_msg is not None:
        streamed_items_buffer.append(user_msg)

    # Track usage across turns
    last_usage_totals = {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 0},
    }
    turn_counter = 0

    # Track latest model info seen during the run (can change across turns)
    resolved_max_output_tokens = max_output_tokens
    if resolved_max_output_tokens is None:
        resolved_max_output_tokens = getattr(
            getattr(agent, "model_settings", None), "max_tokens", None
        )

    last_model_info = {
        "model": model_label or getattr(agent, "model", None),
        "max_input_tokens": max_input_tokens,
        "max_output_tokens": resolved_max_output_tokens,
        "truncation": getattr(
            getattr(agent, "model_settings", None), "truncation", None
        ),
    }

    # Counter for outer-loop iterations so we know when to drop the seed
    # user message from the buffer (on iteration 0 we keep it; on resume
    # iterations after an approval the SDK has already persisted prior
    # items so we wipe the buffer).
    outer_iteration = 0

    try:
        # Use Runner to run the agent
        from agents import RunConfig

        run_config_kwargs = {
            "group_id": group_id or f"session_{session.id}",
            "workflow_name": workflow_name or "omniagents_conversation",
            "call_model_input_filter": _strip_phase_from_model_input,
        }
        if model_provider is not None:
            run_config_kwargs["model_provider"] = model_provider
        if nest_handoff_history:
            run_config_kwargs["nest_handoff_history"] = True
        # Coerce a YAML dict spec into ToolExecutionConfig if needed
        if tool_execution_config is not None:
            try:
                from agents.run_config import ToolExecutionConfig

                if isinstance(tool_execution_config, dict):
                    run_config_kwargs["tool_execution"] = ToolExecutionConfig(
                        **{
                            k: v
                            for k, v in tool_execution_config.items()
                            if k in {"max_function_tool_concurrency"}
                        }
                    )
                else:
                    run_config_kwargs["tool_execution"] = tool_execution_config
            except Exception as e:
                logger.warning(f"Failed to apply tool_execution config: {e}")
        # Sandbox (SDK 0.14+): pass through pre-built SandboxRunConfig.
        if sandbox_run_config is not None:
            run_config_kwargs["sandbox"] = sandbox_run_config
        run_config = RunConfig(**run_config_kwargs)

        # Import Runner locally to avoid module-level import
        from agents import Runner as _Runner

        run_input: Any = agent_input
        result = None

        while True:
            run_kwargs: dict[str, Any] = {
                "max_turns": max_turns,
                "run_config": run_config,
            }
            # Seed ``context`` only on the initial call. On resume the SDK's
            # ``resolve_resumed_context`` replaces ``run_state._context`` with
            # a fresh ``RunContextWrapper`` whenever ``context is not None``,
            # which throws away the approval entries that ``state.approve()``
            # just wrote — the SDK then re-emits the same ToolApprovalItem on
            # the next stream and the UI dialog reappears after the user
            # already answered it.
            if not isinstance(run_input, RunState):
                run_kwargs["context"] = context
            if error_handlers:
                run_kwargs["error_handlers"] = error_handlers
            if run_hooks is not None:
                run_kwargs["hooks"] = run_hooks
            # Always hand the SDK a session it can read/write through.
            # ``sdk_session`` is the spec-supplied backend or compaction
            # wrapper when configured, otherwise the OmniAgents Session
            # itself (which implements the SDK protocol).
            run_kwargs["session"] = sdk_session if sdk_session is not None else session
            # On the *second* and subsequent iterations of this outer loop
            # (after an approval interruption and resume), the SDK has
            # already persisted everything streamed so far, so we drop the
            # buffer to avoid re-flushing prior-turn items in the finally.
            # The first iteration keeps the seed user message in the
            # buffer so an early cancellation still records the input.
            if outer_iteration > 0:
                streamed_items_buffer.clear()
            outer_iteration += 1
            result = _Runner.run_streamed(agent, run_input, **run_kwargs)
            session.current_stream_result = result

            async for event in result.stream_events():
                # Handle raw response events to emit usage on turn completion
                if event.type == "raw_response_event":
                    data = getattr(event, "data", None)
                    if (
                        isinstance(data, ResponseCompletedEvent)
                        and data.response
                        and data.response.usage
                    ):
                        # Use the usage directly from OpenAI's response (already cumulative)
                        # This bypasses the agents library's incorrect double-accumulation
                        raw_usage = data.response.usage
                        # Try to capture the actual model used for this turn (if provided by provider)
                        model_used = getattr(data.response, "model", None)
                        if model_used:
                            last_model_info["model"] = model_used
                        current_totals = {
                            "requests": turn_counter
                            + 1,  # Increment request count ourselves
                            "input_tokens": raw_usage.input_tokens,
                            "output_tokens": raw_usage.output_tokens,
                            "total_tokens": raw_usage.total_tokens,
                            "input_tokens_details": {
                                "cached_tokens": (
                                    getattr(
                                        raw_usage.input_tokens_details,
                                        "cached_tokens",
                                        0,
                                    )
                                    if raw_usage.input_tokens_details
                                    else 0
                                )
                            },
                            "output_tokens_details": {
                                "reasoning_tokens": (
                                    getattr(
                                        raw_usage.output_tokens_details,
                                        "reasoning_tokens",
                                        0,
                                    )
                                    if raw_usage.output_tokens_details
                                    else 0
                                )
                            },
                        }
                        delta = {
                            "requests": current_totals["requests"]
                            - last_usage_totals["requests"],
                            "input_tokens": current_totals["input_tokens"]
                            - last_usage_totals["input_tokens"],
                            "output_tokens": current_totals["output_tokens"]
                            - last_usage_totals["output_tokens"],
                            "total_tokens": current_totals["total_tokens"]
                            - last_usage_totals["total_tokens"],
                            "input_tokens_details": {
                                "cached_tokens": current_totals["input_tokens_details"][
                                    "cached_tokens"
                                ]
                                - last_usage_totals["input_tokens_details"][
                                    "cached_tokens"
                                ],
                            },
                            "output_tokens_details": {
                                "reasoning_tokens": current_totals[
                                    "output_tokens_details"
                                ]["reasoning_tokens"]
                                - last_usage_totals["output_tokens_details"][
                                    "reasoning_tokens"
                                ],
                            },
                        }
                        turn_counter += 1
                        usage_payload = {
                            "run_id": asyncio.current_task().get_name(),
                            "session_id": session.id,
                            "turn": turn_counter,
                            "delta": delta,
                            "totals": current_totals,
                            "response_id": getattr(data.response, "id", None),
                            # Optional model info so clients can adjust UI per-turn
                            "model": last_model_info.get("model"),
                            # Catalog identity (normalized model ref) so
                            # usage can be attributed to a catalog entry
                            # even mid-session after a model switch.
                            "model_ref": getattr(session, "active_model", None),
                            "max_input_tokens": last_model_info.get("max_input_tokens"),
                            "max_output_tokens": last_model_info.get(
                                "max_output_tokens"
                            ),
                            "truncation": last_model_info.get("truncation"),
                        }
                        # Drop None values to avoid sending JSON null for optional fields
                        for key in [
                            "model",
                            "model_ref",
                            "max_input_tokens",
                            "max_output_tokens",
                            "truncation",
                        ]:
                            if usage_payload.get(key) is None:
                                usage_payload.pop(key, None)
                        await _broadcast(session, "token", usage_payload)
                        if service is not None:
                            try:
                                emit = getattr(service, "emit_runtime_hook", None)
                                if emit is not None:
                                    await emit(
                                        "token",
                                        session=session,
                                        run_id=asyncio.current_task().get_name(),
                                        params=usage_payload,
                                    )
                            except Exception:
                                pass
                        last_usage_totals = current_totals
                    # Continue to next event after handling raw response
                    continue

                # Skip low-level / uninterested event types
                if event.type not in ("run_item_stream_event",):
                    continue

                item = event.item
                method, payload = _stream_event_payload(
                    item,
                    run_id=asyncio.current_task().get_name(),
                    session_id=session.id,
                    session=session,
                )

                # Track tool calls that need results
                if method == "tool_called" and payload.get("call_id"):
                    call_id = payload["call_id"]
                    tool_name = payload.get("tool", "unknown")
                    pending_tool_calls[call_id] = tool_name
                    # If the tool declares a running summary, attach it to the
                    # ``tool_called`` payload so clients can title the in-flight
                    # tool card with human phrasing. The ``tool_result`` metadata
                    # supersedes it wholesale on completion. (This rides the
                    # event it describes — per-call, so parallel tool calls
                    # don't clobber each other the way the old global
                    # ``ui.set_status`` side-channel did.)
                    try:
                        # Attempt to find a matching tool object on the agent
                        for t in getattr(agent, "tools", []) or []:
                            name = getattr(t, "name", None)
                            if name == tool_name and hasattr(t, "_running_summary"):
                                text = str(getattr(t, "_running_summary"))
                                if text:
                                    payload["metadata"] = {"summary": text}
                                break
                    except Exception:
                        # Best-effort; ignore if lookup fails
                        pass
                elif method == "tool_result" and payload.get("call_id"):
                    # Tool got a result, remove from pending
                    pending_tool_calls.pop(payload["call_id"], None)

                if method:
                    delivered = await _broadcast(session, method, payload)
                    if not delivered:
                        session.pending_events.append((method, payload))

                    # Emit audit events for tool_called / tool_result
                    if service is not None and method in ("tool_called", "tool_result"):
                        try:
                            from omniagents.core.audit.events import (
                                make_audit_event,
                                truncate_for_audit,
                                TOOL_CALLED,
                                TOOL_RESULT,
                            )

                            _et = (
                                TOOL_CALLED if method == "tool_called" else TOOL_RESULT
                            )
                            _max = 4096
                            if method == "tool_called":
                                _details = {
                                    "tool": payload.get("tool"),
                                    "call_id": payload.get("call_id"),
                                    "input": truncate_for_audit(
                                        payload.get("input", ""), _max
                                    ),
                                }
                            else:
                                _details = {
                                    "tool": payload.get("tool"),
                                    "call_id": payload.get("call_id"),
                                    "output": truncate_for_audit(
                                        payload.get("output", ""), _max
                                    ),
                                }
                            await service.emit_runtime_hook(
                                "audit",
                                **make_audit_event(
                                    _et,
                                    session_id=getattr(session, "id", None),
                                    user_id=getattr(session, "user_id", None),
                                    run_id=payload.get("run_id"),
                                    details=_details,
                                ),
                            )
                        except Exception:
                            pass

                # Persistence is handled by the SDK via ``add_items`` /
                # ``save_result_to_session`` at turn boundaries (or on
                # interruption), not per stream event.  Wire-event delivery
                # to the UI is what makes updates feel live; on-disk
                # persistence is allowed to lag behind by a turn.
                #
                # Buffer the input form of each streamed item so the
                # ``finally`` block can flush them on cancellation paths
                # where the SDK never reaches its turn-boundary save.
                if hasattr(item, "to_input_item"):
                    try:
                        inp = item.to_input_item()
                        if inp:
                            streamed_items_buffer.append(inp)
                    except Exception:
                        pass

                if method:
                    Debug.log(
                        f"[srv]   → {method} run={payload.get('run_id','')} details={payload}"
                    )

            # ----- Handle tool-approval interruptions (SDK 0.16+) -----
            interruptions = list(getattr(result, "interruptions", []) or [])
            if not interruptions:
                break

            # Detect hosted-MCP approval interruptions (raw_item is a
            # ``McpApprovalRequest``). These take a different RPC route so
            # the wire payload carries server label / tool name / arguments
            # instead of the function-tool ``call_id``-based shape.
            try:
                from openai.types.responses.response_output_item import (
                    McpApprovalRequest as _McpApprovalRequest,
                )
            except Exception:  # pragma: no cover - SDK should always have this
                _McpApprovalRequest = None  # type: ignore[assignment]

            def _is_mcp_approval(it: Any) -> bool:
                raw = getattr(it, "raw_item", None)
                if _McpApprovalRequest is not None and isinstance(
                    raw, _McpApprovalRequest
                ):
                    return True
                if isinstance(raw, dict):
                    provider_data = raw.get("provider_data") or {}
                    if (
                        raw.get("type") == "hosted_tool_call"
                        and provider_data.get("type") == "mcp_approval_request"
                    ):
                        return True
                return False

            if service is None:
                # No service to mediate approval — reject everything to keep
                # the run from hanging.
                state = result.to_state()
                for item in interruptions:
                    state.reject(
                        item,
                        rejection_message="Tool approval unavailable in this context",
                    )
                run_input = state
                continue

            current_run_id = (
                asyncio.current_task().get_name() if asyncio.current_task() else None
            )
            state = result.to_state()
            for item in interruptions:
                raw = getattr(item, "raw_item", None)

                if _is_mcp_approval(item):
                    # Hosted-MCP approval: ``raw_item`` is a
                    # ``McpApprovalRequest`` with ``id``, ``server_label``,
                    # ``name`` and ``arguments``.
                    if isinstance(raw, dict):
                        request_id = raw.get("id") or raw.get("approval_request_id")
                        server_label = raw.get("server_label") or "mcp"
                        mcp_tool_name = raw.get("name") or "mcp_tool"
                        mcp_arguments = raw.get("arguments")
                    else:
                        request_id = getattr(raw, "id", None) or getattr(
                            raw, "approval_request_id", None
                        )
                        server_label = getattr(raw, "server_label", None) or "mcp"
                        mcp_tool_name = getattr(raw, "name", None) or "mcp_tool"
                        mcp_arguments = getattr(raw, "arguments", None)

                    if not request_id:
                        state.reject(
                            item,
                            rejection_message=(
                                "MCP approval request missing id; auto-rejected"
                            ),
                        )
                        continue

                    response = await service.request_mcp_approval(
                        session=session,
                        request_id=str(request_id),
                        server_label=str(server_label),
                        tool_name=str(mcp_tool_name),
                        arguments=mcp_arguments,
                        run_id=current_run_id,
                    )
                    if response.get("decision") == "approve":
                        state.approve(item, always_approve=False)
                    else:
                        state.reject(
                            item,
                            rejection_message=response.get("rejection_message"),
                        )
                    continue

                # ----- Function-tool / generic approval path -----
                tool_call_id = getattr(raw, "call_id", None) or getattr(raw, "id", None)
                if not tool_call_id:
                    # Without a stable call_id we can't correlate a response.
                    state.reject(
                        item,
                        rejection_message="Tool call missing call_id; auto-rejected",
                    )
                    continue
                tool_name = item.tool_name or getattr(raw, "name", None) or "tool"
                # The SDK evaluated ``needs_approval`` for every parallel
                # call in this batch before the first prompt was answered.
                # An "Always" on an earlier item lands in the session's
                # allow-set mid-batch — re-check here so the remaining
                # same-tool interruptions approve without prompting. MCP
                # grants are tuple-keyed by (server, tool); everything
                # else keys on the flat name.
                mcp_identity = lookup_identity(session, tool_name)
                if (
                    session_allows_mcp_tool(session, *mcp_identity)
                    if mcp_identity is not None
                    else session_allows_tool(session, tool_name)
                ):
                    state.approve(item, always_approve=False)
                    continue
                arguments = getattr(raw, "arguments", None)
                response = await service.request_tool_approval(
                    session=session,
                    call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    run_id=current_run_id,
                )
                if response.get("decision") == "approve":
                    state.approve(item, always_approve=False)
                else:
                    state.reject(
                        item,
                        rejection_message=response.get("rejection_message"),
                    )

            run_input = state
            session.current_stream_result = None
            # Loop and resume with the approval-applied state.

    except asyncio.CancelledError:
        # Run was cancelled - the finally block will handle pending tool calls
        Debug.log("[srv] ✗ Run cancelled")
        # Re-raise to let the caller handle it
        raise
    except Exception as e:
        # Notify user-defined runtime "error" hooks so they can react.
        # Network/timeout/retry-after retries live behind the SDK
        # ``retry_policy`` knob; context-window recovery has no SDK
        # equivalent for non-Responses providers (LiteLLM, any-llm, etc.),
        # so we keep a hook-driven retry primitive: the hook may inspect
        # ``params["is_context_length_exceeded"]``, perform compaction, and
        # return ``{"retry": True}`` to re-enter the run with the (now
        # smaller) session history.
        # The hook runs for prompt-bearing runs of BOTH shapes: fresh user
        # turns (include_user_message=True) and resume-from-history runs
        # (assistant-role notification/wakeup batches, whose prompt was
        # appended to session history by ``start_run`` before the run began).
        # Resident wakeups are exclusively the second shape — gating on
        # include_user_message used to skip them, so a day session that
        # outgrew the context window while the agent was parked could never
        # reach the compaction hook and bricked on every wakeup at turn 0.
        # Loop protection is ``context_error_retry`` alone: the hook-driven
        # retry re-enters with it set, so one recovery attempt per run.
        if not context_error_retry and service is not None and session is not None:
            try:
                invoke = getattr(service, "invoke_runtime_hooks", None)
                if invoke is not None:
                    from omniagents.core.runtime.compaction import (
                        is_context_length_exceeded as _is_ctx_len,
                    )

                    hook_result = await invoke(
                        "error",
                        session=session,
                        run_id=(
                            asyncio.current_task().get_name()
                            if asyncio.current_task()
                            else ""
                        ),
                        params={
                            "prompt": prompt,
                            "error": e,
                            "is_context_length_exceeded": _is_ctx_len(e),
                        },
                    )
                    if (
                        isinstance(hook_result, dict)
                        and hook_result.get("retry") is True
                    ):
                        return await stream_agent(
                            agent,
                            prompt,
                            session,
                            rpc_channel,
                            service=service,
                            max_turns=max_turns,
                            context=context,
                            max_input_tokens=max_input_tokens,
                            max_output_tokens=max_output_tokens,
                            include_user_message=False,
                            context_error_retry=True,
                            workflow_name=workflow_name,
                            group_id=group_id,
                            user_content=user_content,
                            model_provider=model_provider,
                            model_label=model_label,
                            nest_handoff_history=nest_handoff_history,
                            tool_execution_config=tool_execution_config,
                            error_handlers=error_handlers,
                            run_hooks=run_hooks,
                            sdk_session=sdk_session,
                            sandbox_run_config=sandbox_run_config,
                        )
            except Exception as hook_err:
                # Best-effort: the original error path below still runs. But
                # never swallow silently — an invisible hook failure here
                # cost a day of bricked wakeups before it was found.
                logger.error(
                    f"context-recovery error hook failed: {hook_err}", exc_info=True
                )

        # Any other exception - the finally block will handle pending tool calls
        Debug.log(f"[srv] ✗ Error during run: {e}")

        # Log detailed error context for debugging
        logger.error(f"API request failed: {e}", exc_info=True)

        # If it's an APIError, log the actual error body from OpenAI
        from openai import APIError

        if isinstance(e, APIError):
            logger.error(f"OpenAI API Error Body: {e.body}")
            logger.error(f"OpenAI API Error Type: {e.type}")
            logger.error(f"OpenAI API Error Code: {e.code}")
            Debug.log(f"[srv] ✗ API Error body={e.body} type={e.type} code={e.code}")

        import traceback

        Debug.log(f"[srv] ✗ Traceback:\n{traceback.format_exc()}")

        logger.error(
            f"Error context - Session: {session.id}, "
            f"Turn: {turn_counter}, "
            f"Total items sent: {len(agent_input)}, "
            f"From DB: {history_from_db}, "
            f"Added this turn: {items_this_turn}"
        )

        # Log full item sequence on error (all fields, not just known ones)
        if logger.isEnabledFor(logging.ERROR):
            import json

            error_sequence = []
            for idx, item in enumerate(agent_input):
                # Truncate content fields to keep log readable
                item_copy = dict(item)
                if "content" in item_copy and isinstance(item_copy["content"], str):
                    if len(item_copy["content"]) > 200:
                        item_copy["content"] = (
                            item_copy["content"][:200] + "... (truncated)"
                        )

                # Dump the entire item as JSON to see all fields
                item_json = json.dumps(item_copy, indent=4, default=str)
                error_sequence.append(f"  [{idx}]:\n{item_json}")

            logger.error(f"Full item sequence at error:\n" + "\n".join(error_sequence))

        # Re-raise the original exception
        raise
    finally:
        # The exception this teardown is unwinding, if any. Captured here,
        # before anything below awaits, because inside an ``except`` block
        # ``sys.exc_info()`` reports the newly-caught exception instead.
        # Every await in this block runs while the run's own outcome is
        # already decided, so nothing here may speak for it.
        unwinding = sys.exc_info()[1]

        # Close the run-diff scope FIRST. It re-reads every tracked path,
        # so it must run while the run's workspace is still installed
        # (restored at the bottom of this block) — and it must run before
        # any OTHER await here, because an exception escaping one of those
        # would skip it and leak the mutation-observer contextvar into the
        # next run.
        try:
            await run_diff_scope.aclose()
        except Exception:
            logger.exception("run diff scope teardown failed")

        session.current_stream_result = None
        # Flush the streamed-items buffer.  In the normal SDK-driven
        # flow, the SDK has already called ``Session.add_items`` for
        # each turn boundary — those items are already in
        # ``session.history`` and the dedupe loop below skips them.
        # The buffer's real job is to cover the cancellation/exception
        # window where the SDK never reached its turn-boundary save.
        if streamed_items_buffer:
            # Best-effort dedupe: skip any item whose stable identity
            # (call_id for tool calls/outputs, id for messages /
            # reasoning) is already represented in session.history.
            # User messages have neither, so we match on (role,
            # content) instead.
            existing_ids: set[str] = set()
            existing_call_ids: set[str] = set()
            existing_user_signatures: set[tuple] = set()
            for h in session.history:
                if not isinstance(h, dict):
                    continue
                iid = h.get("id")
                if isinstance(iid, str) and iid:
                    existing_ids.add(iid)
                cid = h.get("call_id")
                if isinstance(cid, str) and cid:
                    existing_call_ids.add(cid)
                if h.get("role") == "user":
                    try:
                        existing_user_signatures.add(("user", repr(h.get("content"))))
                    except Exception:
                        pass
            for buffered in streamed_items_buffer:
                if not isinstance(buffered, dict):
                    continue
                iid = buffered.get("id")
                if isinstance(iid, str) and iid and iid in existing_ids:
                    continue
                cid = buffered.get("call_id")
                btype = buffered.get("type", "")
                # function_call_output is keyed only by call_id; for the
                # call itself we already check id above.
                if (
                    isinstance(btype, str)
                    and btype.endswith("_call_output")
                    and isinstance(cid, str)
                    and cid in existing_call_ids
                ):
                    continue
                if buffered.get("role") == "user":
                    try:
                        sig = ("user", repr(buffered.get("content")))
                    except Exception:
                        sig = None
                    if sig is not None and sig in existing_user_signatures:
                        continue
                try:
                    session.append_message(buffered)
                except Exception:
                    pass
            streamed_items_buffer.clear()

        # Handle any pending tool calls that didn't get results.
        # This covers both cancellation and unexpected termination —
        # without it the next run would see an unresolved
        # ``function_call`` in history and the API would reject it.
        if pending_tool_calls:
            Debug.log(
                f"[srv] ✗ Run ended with {len(pending_tool_calls)} pending tool calls"
            )

            for call_id, tool_name in pending_tool_calls.items():
                # Add a tool result indicating the run was interrupted
                cancelled_result = {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": "[Cancelled by user]",
                }
                session.append_message(cancelled_result)

                # Also send the result as an event to the client
                cancelled_payload = {
                    "run_id": (
                        asyncio.current_task().get_name()
                        if asyncio.current_task()
                        else ""
                    ),
                    "session_id": session.id,
                    "tool": tool_name,
                    "output": "[Cancelled by user]",
                    "call_id": call_id,
                }
                try:
                    await _broadcast(session, "tool_result", cancelled_payload)
                except BaseException as exc:
                    # Same exposure as the run-diff teardown, and it needs
                    # the SAME treatment: this awaits while the run is
                    # already unwinding, so a fault here would replace the
                    # run's own outcome. ``except Exception`` covers the
                    # transport half but NOT a ``CancelledError`` delivered
                    # during the send — that still escaped and rewrote a
                    # RuntimeError run as a cancellation. A genuine
                    # cancellation with nothing in flight still propagates;
                    # ordinary faults never escape. Pre-existing exposure,
                    # fixed here because the run-diff work put an await in
                    # the same teardown.
                    logger.exception(
                        "cancelled tool_result broadcast failed",
                        extra={"call_id": call_id},
                    )
                    if unwinding is None and not isinstance(exc, Exception):
                        raise

                Debug.log(
                    f"[srv]   → tool_result (cancelled) call_id={call_id} tool={tool_name}"
                )

        Debug.log(
            f"[srv] ◀ end_run    session={session.id[:8]} hist={len(session.history)})"
        )

        if token is not None:
            try:
                reset_artifact_callback(token)
            except Exception:
                pass

        if workspace_token is not None:
            try:
                from omniagents.workspace import reset_workspace as _reset_workspace

                _reset_workspace(workspace_token)
            except Exception:
                pass

    return {
        "usage": last_usage_totals,
        "model": last_model_info.get("model"),
        "max_input_tokens": last_model_info.get("max_input_tokens"),
        "max_output_tokens": last_model_info.get("max_output_tokens"),
        "truncation": last_model_info.get("truncation"),
    }
