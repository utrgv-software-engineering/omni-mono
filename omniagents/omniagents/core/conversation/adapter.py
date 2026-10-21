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
  it (legacy ``tool:<call_id>``). Modern live records include the run ID;
  raw legacy history cannot reconstruct that identity. Projection is a fallback,
  never a merge into a recorded thread.
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
    "is_turn_boundary",
    "is_user_row",
    "project_thread",
    "truncate_history_at_user_row",
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


def is_user_row(raw: Any) -> bool:
    """Whether a raw history row is a user message.

    Two consumers, and they mean subtly different things by it:

    * The legacy **projection** treats it as a turn boundary — legacy
      history carries no run correlation, so a user message is the only
      signal in the rows that a new run began.
    * The fork **truncation** treats it as what it literally is: one row
      that corresponds one-to-one with one ``user_message`` item.

    Those coincide for a thread whose every turn has exactly one user
    message, and diverge the moment ``send_user_message`` appends a
    mid-run one. The truncation therefore counts USER ROWS against USER
    MESSAGE ITEMS — never against turns — so the two sequences cannot
    drift. See ``truncate_history_at_user_row``.
    """
    if not isinstance(raw, dict):
        return False
    return (raw.get("type") or "message") == "message" and raw.get("role") == "user"


#: Retained name for the projection's reading of the same predicate.
is_turn_boundary = is_user_row


def truncate_history_at_user_row(
    history: Iterable[Dict[str, Any]],
    user_row_count: int,
    *,
    include_next_row: bool = False,
) -> List[Dict[str, Any]]:
    """The prefix of *history* holding the first *user_row_count* user rows.

    Used by fork-from-turn: the child's DOMAIN rows are truncated at the
    branch point, and its raw history — which is what the model actually
    receives as context on the next run — has to stop at the same place.
    A child whose transcript ends at turn 3 while its context still holds
    turns 4-9 is not a branch, it is a mislabelled copy.

    The unit is the **user row**, not the turn, and that distinction is the
    whole correctness argument. ``send_user_message`` appends a
    ``role: "user"`` history row *inside* a running turn, so a thread can
    hold more user rows than turns; counting turns and cutting on user rows
    then lands the knife in the wrong place and the child displays a turn
    it does not have in context. A user row corresponds one-to-one with a
    ``user_message`` ITEM — including the mid-run ones — so counting those
    is the correspondence that actually holds.

    ``user_row_count <= 0`` with ``include_next_row`` false yields an empty
    history. Rows before the first user row (a system preamble, or an
    assistant-role wakeup that opened the conversation) belong to the first
    segment.

    ``include_next_row`` additionally keeps the single row that OPENS
    segment ``user_row_count + 1``. That is the exact history for a branch
    taken AT a user message ("re-ask from here"): the child holds
    everything before the question, plus the question, and none of the
    answer.
    """
    rows = [row for row in history if isinstance(row, dict)]
    # No early return for ``user_row_count == 0``: a thread whose turns were
    # all opened by assistant-role wakeups has NO user rows at all, and for
    # it "keep everything up to the 1st user row" means keep everything.
    # Short-circuiting to [] would hand such a fork an empty context.
    seen = 0
    for index, row in enumerate(rows):
        if is_user_row(row):
            seen += 1
            if seen > user_row_count:
                return rows[: index + 1] if include_next_row else rows[:index]
    return rows


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
    seen_call_ids: set[str] = set()

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

        if is_turn_boundary(raw):
            # Turn boundary: legacy history has no run_id, and a user
            # message is the only signal in the raw rows that a new run
            # began. Documented as inference (source="adapter"). The
            # predicate is shared with ``truncate_history_at_user_row`` so a
            # fork cannot cut somewhere the projection would not.
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
                if isinstance(call_id, str) and call_id and call_id not in seen_call_ids
                else f"legacy:{index}"
            )
            if isinstance(call_id, str) and call_id:
                seen_call_ids.add(call_id)
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
