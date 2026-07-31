"""Thread / Turn / Item — the canonical conversation domain objects.

Design constraints, in the order they mattered:

**Promotion, not invention.** Every field below already exists somewhere in
the runtime: ``Thread`` is the ``sessions`` row plus derived counters,
``Turn`` is a run (``run_id`` → ``turn_id``), ``Item`` is one addressable
unit of a turn — a message, a reasoning block, a tool call/result pair, an
approval, an elicitation, an artifact, or a compaction checkpoint. The
mapping is spelled out per-field in ``rpc/protocol.md`` § Conversation
Domain Objects.

**One authoritative final state.** An item is born ``started``, may be
updated any number of times (each update bumps ``revision`` and
``updated_at``), and lands in exactly one terminal status — ``completed``,
``failed``, or ``cancelled``. :func:`Item.with_transition` refuses to move
a terminal item, so a late duplicate event can never rewrite history and a
client that reads the store after the run always sees the same answer.

**Deterministic ids.** ``item_id`` is derived by hashing the thread id with
a natural key built ONLY from fields the originating payload carries — a
tool ``call_id``, an approval/elicitation id, the model's ``msg_*`` id, or
(where the event has no identifier at all) a digest of its content. Never
from a server-side position. The live notification stream, the recorder,
and the legacy adapter therefore all produce the *same* canonical id for
the same logical thing, without a shared allocator, and a client can
compute the id itself to merge live events against a stored page. Ids are
stable across restarts and across backfill. The one cost is spelled out on
:func:`content_key`.

**Open records for evolving payloads.** ``Item.content`` and
``Thread.usage`` are plain JSON objects. The house posture for a surface
Desktop consumes is ``stable`` on entry with open result payloads (see the
#305/#306 precedent) — a closed schema here would force a protocol break
every time a tool grows a field.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any, Dict, Generic, List, Optional, Sequence, TypeVar

__all__ = [
    "ITEM_SCHEMA_VERSION",
    "TERMINAL_ITEM_STATUSES",
    "TERMINAL_TURN_STATUSES",
    "CursorError",
    "Item",
    "ItemKind",
    "ItemStatus",
    "Page",
    "Thread",
    "ThreadStatus",
    "ToolKind",
    "Turn",
    "TurnStatus",
    "classify_tool",
    "TITLE_MAX_LEN",
    "content_key",
    "derive_title",
    "decode_cursor",
    "derive_item_id",
    "derive_legacy_turn_id",
    "encode_cursor",
    "terminal_status_for_end_reason",
]

#: Bumped when the persisted row shape changes in a way readers must know
#: about. Stored on every thread/turn/item row so a future migration can
#: identify pre-migration rows without guessing.
ITEM_SCHEMA_VERSION = 1


class ItemKind(str, Enum):
    """What an item *is*. Closed set — clients switch on it exhaustively."""

    #: A message from the human (or an injected wakeup carrying a role).
    USER_MESSAGE = "user_message"
    #: Model-produced assistant message text.
    AGENT_MESSAGE = "agent_message"
    #: Model reasoning block (``rs_*`` history items).
    REASONING = "reasoning"
    #: A tool invocation and its result, as ONE item with a lifecycle.
    TOOL_CALL = "tool_call"
    #: A tool / hosted-MCP approval prompt and its resolution.
    APPROVAL = "approval"
    #: A structured user elicitation and its resolution.
    ELICITATION = "elicitation"
    #: An agent-produced artifact (``ui.add_artifact``).
    ARTIFACT = "artifact"
    #: A context-compaction checkpoint summary.
    COMPACTION = "compaction"
    #: A durable, structured plan that OUTLIVES the turn that created it.
    #:
    #: Reserved here, deliberately, ahead of the plan-persistence work
    #: (tkt_FqUypyxF2tzC). A plan is not N independent ``tool_call`` items:
    #: it has one stable identity, it accumulates steps across several
    #: turns, and a client must be able to render its current state without
    #: folding transient events — which is the whole point of this model.
    #: Declaring the kind and the long-lived lifecycle rule NOW makes that
    #: ticket additive instead of a breaking change to a ``stable`` surface
    #: (the TypeScript ``ConversationItemKind`` union is closed, so widening
    #: it later would be a lockstep client break).
    #:
    #: Note the axis: ``ItemKind.PLAN`` is the plan itself, while a call to
    #: ``task_create``/``task_update`` is a ``TOOL_CALL`` item carrying
    #: ``content.tool_kind == "plan"``. One is the durable object, the other
    #: is the invocation that mutated it.
    PLAN = "plan"
    #: A run-level unified diff snapshot, aggregating the file changes made
    #: during a turn.
    #:
    #: Reserved for the same reason as :attr:`PLAN`, and for the other half
    #: of the same ticket (tkt_FqUypyxF2tzC): individual edits are already
    #: ``TOOL_CALL`` items with ``content.tool_kind == "file"``, but the
    #: *aggregate* — "what did this whole run change?" — has nowhere to
    #: live. ``Turn`` carries typed run metadata, not free-form content, so
    #: the snapshot has to be an item. Declaring the kind now keeps that
    #: work additive: the TypeScript ``ConversationItemKind`` union is
    #: closed, so widening it later would be a lockstep client break.
    #:
    #: Unlike ``PLAN`` this is turn-scoped, so it needs no ``long_lived``
    #: exemption: it opens at the first edit of a turn, is revised as
    #: further edits land, and settles when the turn ends.
    RUN_DIFF = "run_diff"


class ItemStatus(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: The three ways an item can end. Exactly one is reached, exactly once.
TERMINAL_ITEM_STATUSES = frozenset(
    {ItemStatus.COMPLETED, ItemStatus.FAILED, ItemStatus.CANCELLED}
)


class TurnStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_TURN_STATUSES = frozenset(
    {TurnStatus.COMPLETED, TurnStatus.FAILED, TurnStatus.CANCELLED}
)


class ThreadStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class ToolKind(str, Enum):
    """Coarse tool taxonomy so clients can render without a tool registry."""

    COMMAND = "command"
    FILE = "file"
    SEARCH = "search"
    WEB = "web"
    MCP = "mcp"
    PLAN = "plan"
    ARTIFACT = "artifact"
    WORKER = "worker"
    OTHER = "other"


#: Builtin tool names, grouped. Derived from
#: ``omniagents/builtin/tools/__init__.py`` — these are the actual exported
#: tools, not a guess at what a coding agent might have.
_COMMAND_TOOLS = frozenset(
    {"execute_bash", "bash_status", "bash_logs", "bash_kill", "dispatch"}
)
_FILE_TOOLS = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "apply_patch",
        "read_notebook",
        "edit_notebook",
        "read_image",
        "convert_to_markdown",
        "download_file",
    }
)
_SEARCH_TOOLS = frozenset({"glob_files", "grep_files", "list_directory"})
_WEB_TOOLS = frozenset({"web_search", "web_fetch", "scholar_search", "youtube_search"})
_PLAN_TOOLS = frozenset(
    {"task_create", "task_update", "task_list", "task_get", "goal_complete"}
)
_ARTIFACT_TOOLS = frozenset({"display_artifact"})
_WORKER_TOOLS = frozenset(
    {
        "spawn_worker",
        "wait_for_workers",
        "get_worker_status",
        "send_worker_message",
        "close_worker",
        "resume_worker",
        "cleanup_worker_worktree",
    }
)

#: Flat-wire prefix the MCP loader stamps onto imported tools
#: (``mcp_<server>__<tool>`` — see ``core/agents/mcp.py``).
_MCP_TOOL_PREFIX = "mcp_"


def classify_tool(
    tool_name: str | None, *, server_label: str | None = None
) -> ToolKind:
    """Bucket a tool into a :class:`ToolKind`.

    ``server_label`` (present on MCP-derived ``tool_called``/``tool_result``
    payloads) wins outright: an MCP server's ``write_file`` is an MCP call,
    not a builtin file edit. Otherwise the builtin name sets decide, and the
    flat ``mcp_<server>__`` wire prefix is the fallback signal for payloads
    that predate the structured label fields.
    """
    if server_label:
        return ToolKind.MCP
    name = (tool_name or "").strip()
    if not name:
        return ToolKind.OTHER
    if name in _COMMAND_TOOLS:
        return ToolKind.COMMAND
    if name in _FILE_TOOLS:
        return ToolKind.FILE
    if name in _SEARCH_TOOLS:
        return ToolKind.SEARCH
    if name in _WEB_TOOLS:
        return ToolKind.WEB
    if name in _PLAN_TOOLS:
        return ToolKind.PLAN
    if name in _ARTIFACT_TOOLS:
        return ToolKind.ARTIFACT
    if name in _WORKER_TOOLS:
        return ToolKind.WORKER
    if name.startswith(_MCP_TOOL_PREFIX) and "__" in name:
        return ToolKind.MCP
    return ToolKind.OTHER


#: ``run_end.end_reason`` → the turn's terminal status. Anything not listed
#: is treated as a completion (the runtime's default end reason is
#: ``"completed"``; unknown reasons are not failures).
_END_REASON_STATUS = {
    "completed": TurnStatus.COMPLETED,
    "error": TurnStatus.FAILED,
    "cancelled": TurnStatus.CANCELLED,
    "canceled": TurnStatus.CANCELLED,
    "stopped": TurnStatus.CANCELLED,
    "timeout": TurnStatus.FAILED,
}


def terminal_status_for_end_reason(end_reason: str | None) -> TurnStatus:
    return _END_REASON_STATUS.get((end_reason or "").strip(), TurnStatus.COMPLETED)


def derive_item_id(thread_id: str, natural_key: str) -> str:
    """Canonical, deterministic item id.

    ``sha256(thread_id \\x00 natural_key)`` truncated to 128 bits and
    prefixed. Deterministic derivation is what lets three independent
    producers — the recorder (live), the adapter (legacy projection), and a
    client reading the notification stream — agree on an id without a shared
    sequence. The thread id is mixed in so ids never collide across threads
    even when the natural key (``call_id``, ordinal) repeats.
    """
    digest = hashlib.sha256(
        thread_id.encode("utf-8") + b"\x00" + natural_key.encode("utf-8")
    ).hexdigest()
    return f"itm_{digest[:32]}"


#: Longest thread title derived from a first user message. Matches the
#: session-list preview budget in the storage backends so the two surfaces
#: read consistently.
TITLE_MAX_LEN = 200


def derive_title(text: str | None) -> str | None:
    """Thread title from the conversation's opening user message.

    The same single-line, length-capped shape the session-list preview
    already uses, so a client showing both does not display two different
    "names" for one conversation. ``None`` when there is no text to derive
    from — a title is a convenience, never a required field.
    """
    if not isinstance(text, str):
        return None
    collapsed = " ".join(text.split()).strip()
    if not collapsed:
        return None
    if len(collapsed) > TITLE_MAX_LEN:
        return collapsed[: TITLE_MAX_LEN - 1] + "\u2026"
    return collapsed


def content_key(*parts: object) -> str:
    """A short, stable digest of *parts*, for items with no native id.

    Some conversation events carry no identifier of their own — the model's
    assistant text arrives with a ``msg_*`` id only on providers that mint
    one, and a mid-run user message has nothing but its text. Keying those
    on a digest of what they contain gives two properties that a positional
    counter cannot:

    * **Idempotence.** Re-delivering the same event resolves to the same
      ``item_id`` and updates the existing item instead of appending a
      duplicate.
    * **Client derivability.** A client holds the same payload fields, so it
      can compute the id itself and merge the live stream against a stored
      page without a server round-trip.

    The trade-off is explicit and documented in ``protocol.md``: two
    byte-identical messages in the same turn collapse onto one item. That is
    accepted because the alternative — a server-side ordinal the client
    cannot reproduce — breaks the merge story outright.
    """
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:16]


def derive_legacy_turn_id(thread_id: str, ordinal: int) -> str:
    """Turn id for a turn reconstructed from history that has no ``run_id``.

    Legacy history rows carry no run correlation, so the adapter infers turn
    boundaries (a new turn opens at each user message) and mints a stable id
    from the thread and the inferred ordinal. Prefixed distinctly so an
    operator can always tell an inferred turn from a recorded one.
    """
    digest = hashlib.sha256(
        thread_id.encode("utf-8") + b"\x00legacy-turn\x00" + str(ordinal).encode()
    ).hexdigest()
    return f"trn_legacy_{digest[:24]}"


# ---------------------------------------------------------------------
# Pagination cursors
# ---------------------------------------------------------------------


class CursorError(ValueError):
    """An opaque pagination cursor could not be decoded."""


def encode_cursor(payload: Dict[str, Any]) -> str:
    """Opaque, URL-safe cursor.

    Cursors are keyset positions (``{"seq": 41}``), not offsets: paging a
    thread that is still being written must not skip or repeat rows when new
    items land between requests. Base64 keeps the token opaque so the keyset
    shape stays a server-side implementation detail.
    """
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> Dict[str, Any]:
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding)
        decoded = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise CursorError(f"Malformed pagination cursor: {cursor!r}") from exc
    if not isinstance(decoded, dict):
        raise CursorError(f"Malformed pagination cursor: {cursor!r}")
    return decoded


T = TypeVar("T")


@dataclass(frozen=True)
class Page(Generic[T]):
    """One page of a keyset-paginated read."""

    entries: Sequence[T]
    next_cursor: Optional[str]
    has_more: bool


# ---------------------------------------------------------------------
# The three domain objects
# ---------------------------------------------------------------------


@dataclass
class Thread:
    """A conversation. One-to-one with a session; ``thread_id ==
    session_id`` so every existing session id keeps working as an address.
    """

    thread_id: str
    #: Owner principal, mirrored from ``sessions.user_id``. ``None`` means
    #: definitively unowned (the local single-user topology).
    user_id: Optional[str] = None
    status: ThreadStatus = ThreadStatus.ACTIVE
    title: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0
    #: High-water mark of the thread-monotonic item ordinal. Item ``seq``
    #: values are dense and increasing; this is the allocator's cursor.
    last_seq: int = 0
    turn_count: int = 0
    item_count: int = 0
    #: Branching: set when this thread was created by ``fork_session``.
    parent_thread_id: Optional[str] = None
    #: The item the fork branched *after* — the last item copied from the
    #: parent. Lets a client render a branch point instead of an orphan.
    branched_from_item_id: Optional[str] = None
    #: Aggregated token usage across the thread's turns.
    usage: Dict[str, Any] = field(default_factory=dict)
    #: Compaction state: ``{"checkpoint_item_id", "checkpoint_seq",
    #: "compacted_below_seq"}``. Items below ``compacted_below_seq`` are
    #: still readable but are no longer part of the model's live context.
    compaction: Dict[str, Any] = field(default_factory=dict)
    #: ``"recorded"`` (written live by the recorder) or ``"adapter"``
    #: (projected from legacy history by the compatibility adapter).
    source: str = "recorded"
    #: When this thread's pre-existing history was materialized into domain
    #: objects — or when it was established that there was none to
    #: materialize. ``None`` means the question has not been settled yet.
    #:
    #: This is an EXPLICIT marker on purpose. The guard used to be "does
    #: this thread have any items?", and inferring a lifecycle fact from a
    #: row count is precisely what produced this feature's worst bugs: a
    #: thread that legitimately had zero items read as "not yet
    #: materialized" and got re-projected over live data. A thread row
    #: created while its history happened to be empty, with history
    #: arriving afterwards, would likewise hide that history forever.
    #: Storing the decision removes the inference.
    projected_at: Optional[float] = None
    schema_version: int = ITEM_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "user_id": self.user_id,
            "status": self.status.value,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_seq": self.last_seq,
            "turn_count": self.turn_count,
            "item_count": self.item_count,
            "parent_thread_id": self.parent_thread_id,
            "branched_from_item_id": self.branched_from_item_id,
            "usage": dict(self.usage),
            "compaction": dict(self.compaction),
            "source": self.source,
            "projected_at": self.projected_at,
            "schema_version": self.schema_version,
        }


@dataclass
class Turn:
    """One run of the agent. ``turn_id == run_id`` for recorded turns."""

    turn_id: str
    thread_id: str
    #: 1-based position within the thread. Dense and gap-free, so a client
    #: can address "turn 3" without holding the whole list.
    ordinal: int
    status: TurnStatus = TurnStatus.RUNNING
    created_at: float = 0.0
    updated_at: float = 0.0
    completed_at: Optional[float] = None
    #: The text that started the run (``run_started.prompt``) and the role
    #: it was injected under (``user`` for human turns, ``assistant`` for
    #: notification-batch wakeups).
    prompt: Optional[str] = None
    prompt_role: Optional[str] = None
    end_reason: Optional[str] = None
    error: Optional[Dict[str, Any]] = None
    usage: Dict[str, Any] = field(default_factory=dict)
    model: Optional[str] = None
    model_ref: Optional[str] = None
    item_count: int = 0
    #: First and last item ordinals belonging to this turn — the keyset
    #: window a client uses to fetch exactly this turn's items.
    first_seq: Optional[int] = None
    last_seq: Optional[int] = None
    #: Retry bookkeeping promoted from ``run_status`` (``attempt``/``total``).
    attempts: int = 0
    source: str = "recorded"
    schema_version: int = ITEM_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "thread_id": self.thread_id,
            "ordinal": self.ordinal,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "prompt": self.prompt,
            "prompt_role": self.prompt_role,
            "end_reason": self.end_reason,
            "error": dict(self.error) if self.error else None,
            "usage": dict(self.usage),
            "model": self.model,
            "model_ref": self.model_ref,
            "item_count": self.item_count,
            "first_seq": self.first_seq,
            "last_seq": self.last_seq,
            "attempts": self.attempts,
            "source": self.source,
            "schema_version": self.schema_version,
        }


@dataclass
class Item:
    """One addressable unit of a conversation, with a lifecycle."""

    item_id: str
    thread_id: str
    turn_id: Optional[str]
    #: Thread-monotonic ordinal. The pagination keyset and the *only*
    #: ordering a client should rely on: wall-clock timestamps are not
    #: monotonic under concurrent tool completions.
    seq: int
    kind: ItemKind
    status: ItemStatus = ItemStatus.STARTED
    role: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0
    completed_at: Optional[float] = None
    #: Incremented on every update. A client that missed intermediate
    #: deltas can tell whether its cached copy is stale.
    revision: int = 0
    #: The authoritative payload. Open record: shape varies by ``kind``
    #: (see protocol.md). For a completed item this is everything needed to
    #: render it — no event replay required.
    content: Dict[str, Any] = field(default_factory=dict)
    #: Where this item came from: ``{"call_id", "event", "history_index",
    #: "message_id"}``. Debuggability + the join back to raw history.
    source_ref: Dict[str, Any] = field(default_factory=dict)
    #: Opts the item out of run-end finalization.
    #:
    #: The default lifecycle assumes an item belongs to the turn that
    #: created it, so ``run_end`` resolves anything still open (see
    #: ``ConversationRecorder.close_turn``). Some objects legitimately
    #: outlive their turn — a durable plan accumulates steps across many
    #: runs and must stay open to accept them. Marking such an item
    #: long-lived exempts it from that sweep; it is then the owning
    #: subsystem's job to land it terminally when the object is genuinely
    #: done. Set on the item, not inferred from ``kind``, so the exemption
    #: is always a deliberate, inspectable property of the row.
    long_lived: bool = False
    source: str = "recorded"
    schema_version: int = ITEM_SCHEMA_VERSION

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_ITEM_STATUSES

    def with_transition(
        self,
        *,
        status: ItemStatus | None = None,
        content: Dict[str, Any] | None = None,
        now: float,
        merge_content: bool = True,
    ) -> "Item":
        """Return the item advanced by one lifecycle step.

        Refuses to move an already-terminal item: the first terminal state
        an item reaches is the authoritative one. A duplicate ``tool_result``
        (replay, double-delivery, a retried run reusing a ``call_id``)
        therefore cannot rewrite a completed item, which is precisely the
        guarantee that makes store-only reconstruction trustworthy.
        """
        if self.is_terminal:
            return self
        merged = dict(self.content)
        if content is not None:
            if merge_content:
                merged.update(content)
            else:
                merged = dict(content)
        next_status = status or self.status
        return Item(
            item_id=self.item_id,
            thread_id=self.thread_id,
            turn_id=self.turn_id,
            seq=self.seq,
            kind=self.kind,
            status=next_status,
            role=self.role,
            created_at=self.created_at,
            updated_at=now,
            completed_at=(
                now if next_status in TERMINAL_ITEM_STATUSES else self.completed_at
            ),
            revision=self.revision + 1,
            content=merged,
            source_ref=dict(self.source_ref),
            long_lived=self.long_lived,
            source=self.source,
            schema_version=self.schema_version,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "seq": self.seq,
            "kind": self.kind.value,
            "status": self.status.value,
            "role": self.role,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "revision": self.revision,
            "content": dict(self.content),
            "source_ref": dict(self.source_ref),
            "long_lived": self.long_lived,
            "source": self.source,
            "schema_version": self.schema_version,
        }


def items_to_dicts(items: List[Item]) -> List[Dict[str, Any]]:
    return [item.to_dict() for item in items]
