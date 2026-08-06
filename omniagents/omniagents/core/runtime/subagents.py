"""The subagent observability bus.

Observability attaches to *a nested agent run*, not to the machinery that
manages it. Every flavor of subagent — background workers (WorkerManager),
agents exposed as tools (``Agent.as_tool``) — produces the same narrative
event vocabulary here, and clients consume ONE surface:

- ``ui.subagent.event``   — one narrative event from a subagent's run,
  tagged with ``subagent_id`` and ``kind``. Methods reuse the GUI
  notification vocabulary (``tool_called`` / ``tool_result`` /
  ``message_output`` / ``run_*``), so clients parse them with the code
  they already have for the main run.
- ``ui.subagents.update`` — the merged snapshot of all subagents for a
  session (workers with lifecycle verbs, agent-tool runs mid-turn).

Producers adapt their native stream into the bus:
- ``WorkerManager`` relays its monitor events (see
  ``builtin/tools/worker_tools.py``), contributing snapshot entries via a
  registered provider.
- The agent builder installs :func:`agent_tool_stream_observer` as the
  ``on_stream`` hook of every observable ``agent_tools`` entry; the run is
  settled by the parent's own ``tool_result`` for the outer call
  (:func:`note_parent_tool_result`, called from the bridge).

Everything here is best-effort: a lost event or snapshot costs display
fidelity, never correctness.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Narrative events worth relaying to clients. ``token`` is deliberately
# excluded (per-token chatter); approvals are answered locally by each
# producer, not observed.
RELAYED_SUBAGENT_EVENTS = frozenset(
    {
        "run_started",
        "run_status",
        "message_output",
        "tool_called",
        "tool_result",
        "run_end",
    }
)

# Ended agent-tool runs kept per session for the snapshot's
# "recently exited" tail (parity with workers, which persist in their
# manager until session end).
_ENDED_KEEP = 20


def _truncate(text: str, limit: int = 200) -> str:
    one_line = " ".join(str(text).split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


# Bounds the stored task text. Generous — clients decide how much to
# SHOW (list rows truncate, detail panes wrap the whole thing); the bus
# only guards against a pathological payload.
_TASK_CAP = 2000


def _task_from_arguments(arguments: str) -> str:
    """The human task inside a tool call's argument JSON.

    ``Agent.as_tool`` wraps the generated input as ``{"input": "..."}``
    (custom parameter models may use another single string field). Show
    the prose, not the JSON envelope; fall back to the raw text when the
    shape is unrecognized."""
    try:
        parsed = json.loads(arguments or "")
    except Exception:
        return _truncate(arguments or "", _TASK_CAP)
    if isinstance(parsed, dict):
        value = parsed.get("input")
        if isinstance(value, str) and value.strip():
            return _truncate(value, _TASK_CAP)
        strings = [
            v for v in parsed.values() if isinstance(v, str) and v.strip()
        ]
        if len(strings) == 1:
            return _truncate(strings[0], _TASK_CAP)
    return _truncate(arguments or "", _TASK_CAP)


@dataclass
class SubagentRun:
    """One live (or recently ended) agent-tool run."""

    subagent_id: str  # the OUTER tool call's call_id
    agent: str
    task: str
    parent_session_id: Optional[str]
    status: str = "running"  # running | completed | error
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    result: Optional[str] = None
    error: Optional[str] = None

    def snapshot(self) -> dict:
        end = self.finished_at if self.finished_at is not None else time.time()
        return {
            "subagent_id": self.subagent_id,
            "kind": "agent_tool",
            "agent": self.agent,
            "status": self.status,
            "task": self.task,
            "parent_session_id": self.parent_session_id,
            "session_id": "",
            "run_id": self.subagent_id,
            "result": self.result,
            "error": self.error,
            "isolation": None,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "wall_time_ms": int((end - self.started_at) * 1000),
        }


# Live + recently-ended agent-tool runs, keyed by outer call_id.
_AGENT_TOOL_RUNS: Dict[str, SubagentRun] = {}

# Extra snapshot providers: session_id -> list of entry dicts. Workers
# register theirs so the bus composes ONE merged snapshot without the bus
# importing worker machinery.
_SNAPSHOT_PROVIDERS: List[Callable[[Optional[str]], List[dict]]] = []


def register_snapshot_provider(
    provider: Callable[[Optional[str]], List[dict]],
) -> None:
    """Register a provider contributing snapshot entries per session."""
    if provider not in _SNAPSHOT_PROVIDERS:
        _SNAPSHOT_PROVIDERS.append(provider)


def subagents_snapshot(parent_session_id: Optional[str]) -> List[dict]:
    """The merged subagent snapshot for one session, all kinds."""
    entries: List[dict] = []
    for provider in _SNAPSHOT_PROVIDERS:
        try:
            entries.extend(provider(parent_session_id))
        except Exception:
            logger.debug("subagent snapshot provider failed", exc_info=True)
    entries.extend(
        run.snapshot()
        for run in _AGENT_TOOL_RUNS.values()
        if run.parent_session_id == parent_session_id
    )
    return entries


def _current_service() -> Any | None:
    try:
        from omniagents.core.agents.service import current_service

        return current_service()
    except Exception:
        return None


async def broadcast_subagents_snapshot(
    service: Any | None, parent_session_id: Optional[str]
) -> None:
    """Push the merged snapshot to every client of *parent_session_id*."""
    if service is None or not parent_session_id:
        return
    try:
        sess = service.get_session(parent_session_id)
        if sess is None:
            return
        await service.request_client_function(
            "ui.subagents.update",
            {"snapshot": subagents_snapshot(parent_session_id)},
            session=sess,
            expect_result=False,
        )
    except Exception as e:
        logger.warning("ui.subagents.update broadcast failed: %s", e)


async def emit_subagent_event(
    parent_session_id: Optional[str],
    subagent_id: str,
    kind: str,
    method: str,
    params: dict,
) -> None:
    """Relay one narrative event to the parent session's clients."""
    if not parent_session_id:
        return
    service = _current_service()
    if service is None:
        return
    try:
        sess = service.get_session(parent_session_id)
        if sess is None:
            return
        await service.request_client_function(
            "ui.subagent.event",
            {
                "subagent_id": subagent_id,
                "kind": kind,
                "method": method,
                "params": params,
            },
            session=sess,
            expect_result=False,
        )
    except Exception as e:
        logger.debug("ui.subagent.event relay failed: %s", e)


# ---------------------------------------------------------------------------
# Agent-tool producer (Agent.as_tool on_stream)
# ---------------------------------------------------------------------------


def _prune_ended(parent_session_id: Optional[str]) -> None:
    ended = [
        r
        for r in _AGENT_TOOL_RUNS.values()
        if r.parent_session_id == parent_session_id and r.status != "running"
    ]
    ended.sort(key=lambda r: r.finished_at or 0.0)
    for stale in ended[:-_ENDED_KEEP] if len(ended) > _ENDED_KEEP else []:
        _AGENT_TOOL_RUNS.pop(stale.subagent_id, None)


async def agent_tool_stream_observer(payload: dict) -> None:
    """``on_stream`` hook for observable ``agent_tools`` entries.

    Receives the SDK's ``AgentToolStreamEvent`` dict. Registers the run on
    first sight (keyed by the outer call_id), maps ``RunItemStreamEvent``
    items through the bridge's payload mapper (MCP-UI side channel OFF —
    nested runs must not steal the parent's pending UI resources), and
    relays. Runs inside the parent run's task tree, so the session id and
    service resolve from contextvars. The SDK swallows exceptions raised
    here; we still guard so one bad event can't spam the log.
    """
    try:
        event = payload.get("event")
        tool_call = payload.get("tool_call")
        call_id = getattr(tool_call, "call_id", None) or ""
        if not call_id or event is None:
            return

        from omniagents.core.session.helpers import current_session_id

        session_id = current_session_id()
        if session_id is None:
            return  # not inside a framework-managed run — nothing to attach to

        run = _AGENT_TOOL_RUNS.get(call_id)
        if run is None:
            agent_obj = payload.get("agent")
            run = SubagentRun(
                subagent_id=call_id,
                agent=str(getattr(agent_obj, "name", "") or "agent"),
                task=_task_from_arguments(
                    getattr(tool_call, "arguments", "") or ""
                ),
                parent_session_id=session_id,
            )
            _AGENT_TOOL_RUNS[call_id] = run
            await emit_subagent_event(
                session_id,
                call_id,
                "agent_tool",
                "run_started",
                {"run_id": call_id, "agent": run.agent},
            )
            await broadcast_subagents_snapshot(_current_service(), session_id)

        if getattr(event, "type", "") != "run_item_stream_event":
            return
        item = getattr(event, "item", None)
        if item is None:
            return

        from omniagents.core.runtime.bridge import stream_event_payload

        method, mapped = stream_event_payload(
            item,
            run_id=call_id,
            session_id=session_id,
            session=None,
            include_mcp_ui=False,
        )
        if method in RELAYED_SUBAGENT_EVENTS and mapped is not None:
            await emit_subagent_event(
                session_id, call_id, "agent_tool", method, mapped
            )
    except Exception:
        logger.debug("agent-tool stream observer failed", exc_info=True)


async def note_parent_tool_result(call_id: str, payload: dict) -> None:
    """The parent's ``tool_result`` for an observed agent-tool call — its
    end signal. Settles the run, relays ``run_end``, refreshes the
    snapshot. No-op for ordinary (non-agent-tool) calls."""
    run = _AGENT_TOOL_RUNS.get(call_id)
    if run is None or run.status != "running":
        return
    metadata = payload.get("metadata")
    failed = isinstance(metadata, dict) and metadata.get("success") is False
    run.status = "error" if failed else "completed"
    run.finished_at = time.time()
    # The tool's return string IS the sub-agent's answer — carry it so
    # settled runs show their result, not just a status word.
    output = payload.get("output")
    text = output if isinstance(output, str) and output.strip() else None
    if failed:
        run.error = text
    else:
        run.result = text
    await emit_subagent_event(
        run.parent_session_id,
        call_id,
        "agent_tool",
        "run_end",
        {
            "run_id": call_id,
            "end_reason": "error" if failed else "completed",
        },
    )
    await broadcast_subagents_snapshot(_current_service(), run.parent_session_id)
    _prune_ended(run.parent_session_id)
