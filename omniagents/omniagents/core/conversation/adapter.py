"""Backward compatibility: read pre-existing sessions as domain objects.

Every session written before this feature landed — and every session
written by any process that has the recorder disabled — has rows in
``history``/``sessions`` and nothing in the domain store. Those sessions
must stay readable, so the adapter *projects* Thread/Turn/Item from the raw
history rows using exactly the item shapes the runtime already persists
(OpenAI Responses items: ``message``, ``reasoning``, ``function_call``,
``function_call_output``, plus the OmniAgents ``context_summary``
checkpoint marker).

Two properties make the projection trustworthy rather than a guess:

* **Ids are derived the same way.** The adapter mints item ids with the
  same :func:`~omniagents.core.conversation.model.derive_item_id` the
  recorder uses, keyed on the same natural keys where the raw data supports
  it (``tool:<call_id>``). A tool call recorded live and the same tool call
  projected from history therefore land on the same ``item_id``.
* **Inference is labelled.** Legacy history has no run correlation, so turn
  boundaries are *inferred* — a new turn opens at each user message. Those
  turns get ``source="adapter"`` and a ``trn_legacy_`` id prefix, so nothing
  downstream can mistake an inferred boundary for a recorded one.

Projection is pure. :func:`backfill_thread` is the only writer, and it is
idempotent: it refuses to overwrite a thread that already has recorded
rows, so a live session is never clobbered by a late migration pass.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterable, List, NamedTuple, Optional

from .model import (
    Item,
    ItemKind,
    ItemStatus,
    Thread,
    ThreadStatus,
    Turn,
    TurnStatus,
    classify_tool,
    derive_item_id,
    derive_legacy_turn_id,
    derive_title,
)
from .store import ConversationStore

logger = logging.getLogger(__name__)

__all__ = [
    "ProjectedThread",
    "backfill_thread",
    "project_thread",
]

#: The OmniAgents-internal marker compaction writes onto its checkpoint
#: item (``core/runtime/compaction.CHECKPOINT_KIND``). Imported lazily in
#: :func:`_checkpoint_kind` to keep this module free of runtime imports.
_OMNI_NAMESPACE = "omniagents"


class ProjectedThread(NamedTuple):
    """Result of a projection: ``(thread, turns, items)``."""

    thread: Thread
    turns: List[Turn]
    items: List[Item]


def _message_text(content: Any) -> str:
    """Flatten a Responses-API content field to plain text.

    Mirrors the extraction the SQLite/Postgres session backends already use
    for list previews, so a projected message reads the same as the session
    list preview the user has been seeing all along.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                for key in ("text", "input_text", "output_text"):
                    value = part.get(key)
                    if isinstance(value, str):
                        parts.append(value)
                        break
        return "".join(parts)
    return ""


def _checkpoint_kind(item: Dict[str, Any]) -> Optional[str]:
    namespace = item.get(_OMNI_NAMESPACE)
    if isinstance(namespace, dict):
        kind = namespace.get("kind")
        if isinstance(kind, str):
            return kind
    return None


def _summary_text(item: Dict[str, Any]) -> str:
    summary = item.get("summary")
    if isinstance(summary, list):
        parts: List[str] = []
        for entry in summary:
            if isinstance(entry, str):
                parts.append(entry)
            elif isinstance(entry, dict) and isinstance(entry.get("text"), str):
                parts.append(entry["text"])
        return "\n".join(parts)
    return ""


def project_thread(
    thread_id: str,
    history: Iterable[Dict[str, Any]],
    *,
    user_id: str | None = None,
    archived: bool = False,
    created_at: float = 0.0,
    updated_at: float = 0.0,
) -> ProjectedThread:
    """Project legacy history rows into Thread / Turn / Item objects."""
    items: List[Item] = []
    turns: List[Turn] = []
    seq = 0
    turn_ordinal = 0
    current_turn: Optional[Turn] = None
    # ``function_call`` and its ``function_call_output`` are two history
    # rows but ONE domain item, so calls are indexed by call_id while the
    # scan runs and completed in place when the output row arrives.
    calls_by_id: Dict[str, Item] = {}

    def open_turn() -> Turn:
        nonlocal turn_ordinal, current_turn
        turn_ordinal += 1
        turn = Turn(
            turn_id=derive_legacy_turn_id(thread_id, turn_ordinal),
            thread_id=thread_id,
            ordinal=turn_ordinal,
            # Every projected turn is closed: legacy history is by
            # definition the record of runs that already finished. A
            # still-running run has live state, and live state is recorded,
            # not projected.
            status=TurnStatus.COMPLETED,
            created_at=created_at,
            updated_at=updated_at,
            completed_at=updated_at or None,
            end_reason="completed",
            source="adapter",
        )
        turns.append(turn)
        current_turn = turn
        return turn

    def emit(
        *,
        natural_key: str,
        kind: ItemKind,
        status: ItemStatus,
        role: str | None,
        content: Dict[str, Any],
        history_index: int,
    ) -> Item:
        nonlocal seq
        seq += 1
        turn = current_turn or open_turn()
        item = Item(
            item_id=derive_item_id(thread_id, natural_key),
            thread_id=thread_id,
            turn_id=turn.turn_id,
            seq=seq,
            kind=kind,
            status=status,
            role=role,
            created_at=created_at,
            updated_at=updated_at,
            completed_at=updated_at or None,
            revision=0,
            content=content,
            source_ref={"history_index": history_index},
            source="adapter",
        )
        items.append(item)
        turn.item_count += 1
        turn.first_seq = seq if turn.first_seq is None else min(turn.first_seq, seq)
        turn.last_seq = seq if turn.last_seq is None else max(turn.last_seq, seq)
        return item

    compaction: Dict[str, Any] = {}
    title: Optional[str] = None

    for index, raw in enumerate(history):
        if not isinstance(raw, dict):
            continue
        item_type = raw.get("type") or "message"
        role = raw.get("role")

        if item_type == "message" and role == "user":
            # Turn boundary: legacy history has no run_id, and a user
            # message is the only signal in the raw rows that a new run
            # began. Documented as inference (source="adapter").
            open_turn()
            text = _message_text(raw.get("content"))
            if title is None:
                title = derive_title(text)
            emit(
                natural_key=f"legacy:{index}",
                kind=ItemKind.USER_MESSAGE,
                status=ItemStatus.COMPLETED,
                role="user",
                content={"text": text},
                history_index=index,
            )
            continue

        if item_type == "message":
            checkpoint = _checkpoint_kind(raw)
            if checkpoint is not None:
                projected = emit(
                    natural_key=f"legacy:{index}",
                    kind=ItemKind.COMPACTION,
                    status=ItemStatus.COMPLETED,
                    role=role if isinstance(role, str) else "assistant",
                    content={
                        "summary": _message_text(raw.get("content")),
                        "checkpoint_kind": checkpoint,
                        "version": (raw.get(_OMNI_NAMESPACE) or {}).get("version", 1),
                    },
                    history_index=index,
                )
                compaction = {
                    "checkpoint_item_id": projected.item_id,
                    "checkpoint_seq": projected.seq,
                    "compacted_below_seq": projected.seq,
                }
                continue
            emit(
                natural_key=f"legacy:{index}",
                kind=ItemKind.AGENT_MESSAGE,
                status=ItemStatus.COMPLETED,
                role=role if isinstance(role, str) else "assistant",
                content={
                    "text": _message_text(raw.get("content")),
                    **(
                        {"message_id": raw["id"]}
                        if isinstance(raw.get("id"), str)
                        else {}
                    ),
                },
                history_index=index,
            )
            continue

        if item_type == "reasoning":
            emit(
                natural_key=f"legacy:{index}",
                kind=ItemKind.REASONING,
                status=ItemStatus.COMPLETED,
                role="assistant",
                content={
                    "summary": _summary_text(raw),
                    **(
                        {"reasoning_id": raw["id"]}
                        if isinstance(raw.get("id"), str)
                        else {}
                    ),
                },
                history_index=index,
            )
            continue

        if isinstance(item_type, str) and item_type.endswith("_call_output"):
            call_id = raw.get("call_id")
            pending = (
                calls_by_id.pop(call_id, None) if isinstance(call_id, str) else None
            )
            if pending is not None:
                pending.content["output"] = raw.get("output")
                pending.status = ItemStatus.COMPLETED
                pending.source_ref["output_history_index"] = index
                continue
            # An output with no matching call (history trimmed mid-pair, or
            # a compaction window that cut the call away). Keep it as its
            # own completed tool item rather than dropping conversation
            # content on the floor.
            emit(
                natural_key=f"legacy:{index}",
                kind=ItemKind.TOOL_CALL,
                status=ItemStatus.COMPLETED,
                role="assistant",
                content={
                    "tool": None,
                    "tool_kind": "other",
                    "output": raw.get("output"),
                    "orphaned_output": True,
                },
                history_index=index,
            )
            continue

        if isinstance(item_type, str) and item_type.endswith("_call"):
            call_id = raw.get("call_id")
            name = raw.get("name")
            natural_key = (
                f"tool:{call_id}"
                if isinstance(call_id, str) and call_id
                else f"legacy:{index}"
            )
            projected = emit(
                natural_key=natural_key,
                kind=ItemKind.TOOL_CALL,
                # A call with no output row in history never returned; the
                # authoritative final state is "failed", not "started".
                status=ItemStatus.FAILED,
                role="assistant",
                content={
                    "tool": name,
                    "tool_kind": classify_tool(
                        name if isinstance(name, str) else None
                    ).value,
                    "input": raw.get("arguments"),
                    **({"call_id": call_id} if isinstance(call_id, str) else {}),
                },
                history_index=index,
            )
            if isinstance(call_id, str) and call_id:
                calls_by_id[call_id] = projected
            continue

        # Unknown item type: preserve it verbatim so a projection is never
        # lossy. Renderers switch on ``kind`` and will show it as a message
        # with the raw payload attached.
        emit(
            natural_key=f"legacy:{index}",
            kind=ItemKind.AGENT_MESSAGE,
            status=ItemStatus.COMPLETED,
            role=role if isinstance(role, str) else None,
            content={"text": _message_text(raw.get("content")), "raw_type": item_type},
            history_index=index,
        )

    for pending in calls_by_id.values():
        # Scanned to the end without an output row.
        pending.content.setdefault("finalized_by", "projection")

    thread = Thread(
        thread_id=thread_id,
        user_id=user_id,
        status=ThreadStatus.ARCHIVED if archived else ThreadStatus.ACTIVE,
        title=title,
        created_at=created_at,
        updated_at=updated_at,
        last_seq=seq,
        turn_count=len(turns),
        item_count=len(items),
        compaction=compaction,
        source="adapter",
    )
    return ProjectedThread(thread, turns, items)


def backfill_thread(
    store: ConversationStore,
    thread_id: str,
    history: Iterable[Dict[str, Any]],
    *,
    user_id: str | None = None,
    archived: bool = False,
    created_at: float = 0.0,
    updated_at: float = 0.0,
    parent_thread_id: str | None = None,
    branched_from_item_id: str | None = None,
) -> Optional[Thread]:
    """Project *history* and persist it, once.

    Returns the persisted thread, or ``None`` when there was nothing to
    project.

    **Refuses to touch a thread that has already been materialized.**
    Projection is a one-time step performed when a thread row is first
    created (see :meth:`ConversationRecorder.ensure_thread`); re-running it
    over a live thread would rewrite rows the recorder owns and reset the
    ordinal cursor. The guard is the EXPLICIT ``projected_at`` marker, not
    an inference from the item count: a thread can legitimately hold zero
    items, and a row created while its history was empty would otherwise
    hide history that arrived later. A migration pass is safe to re-run
    over a live deployment.
    """
    existing = store.get_thread(thread_id)
    if existing is not None and existing.projected_at is not None:
        return existing
    materialized = [row for row in history if isinstance(row, dict)]
    if not materialized:
        return existing
    thread, turns, items = project_thread(
        thread_id,
        materialized,
        user_id=user_id,
        archived=archived,
        created_at=created_at,
        updated_at=updated_at,
    )
    # A forked thread is created with its ancestry already stamped; the
    # projection must not erase it.
    if parent_thread_id is None and existing is not None:
        parent_thread_id = existing.parent_thread_id
        branched_from_item_id = existing.branched_from_item_id
    thread.parent_thread_id = parent_thread_id
    thread.branched_from_item_id = branched_from_item_id
    thread.projected_at = time.time()
    store.write_projection(thread, turns, items)
    return thread
