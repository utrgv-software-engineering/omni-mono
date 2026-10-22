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
    "SEARCH_TEXT_MAX_LEN",
    "TERMINAL_ITEM_STATUSES",
    "TERMINAL_TURN_STATUSES",
    "CursorError",
    "Item",
    "ItemKind",
    "ItemStatus",
    "Page",
    "SearchHit",
    "Thread",
    "ThreadStatus",
    "ThreadFilter",
    "ToolKind",
    "UNSCOPED",
    "Turn",
    "TurnStatus",
    "classify_tool",
    "TITLE_MAX_LEN",
    "content_key",
    "derive_search_text",
    "derive_title",
    "decode_cursor",
    "decode_thread_cursor",
    "derive_item_id",
    "derive_legacy_turn_id",
    "encode_cursor",
    "encode_thread_cursor",
    "search_terms",
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
    #: A workflow completion-review decision (``plan_completion_reviewed``):
    #: a reviewer's verdict on a task's completion claim — outcome,
    #: reviewer and rationale.
    #:
    #: Append-only and born terminal: it is the record of a decision that
    #: already happened, not a lifecycle, and the same task can be rejected
    #: and later accepted — so each review is its own item rather than an
    #: update to one (see the occurrence-ordinal pattern in the recorder).
    #: Distinct from :attr:`APPROVAL`, which is a *prompt* awaiting its
    #: resolution: nothing was ever pending here.
    WORKFLOW_REVIEW = "workflow_review"


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
# ``glob_files``/``grep_files`` were removed as tools (search is `rg` via
# execute_bash), but stay classified here so sessions recorded before the
# removal still render their calls as SEARCH rather than falling through
# to the unknown-tool bucket.
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


def provider_item_id(value: Any) -> Optional[str]:
    """An item id the provider minted, or ``None``.

    Providers without Responses ids (chat completions, most
    OpenAI-compatible endpoints) get the agents SDK's placeholder on every
    item, so it identifies nothing and must never key an item.
    """
    from agents.models.fake_id import FAKE_RESPONSES_ID

    if isinstance(value, str) and value and value != FAKE_RESPONSES_ID:
        return value
    return None


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


#: Longest per-item text handed to the full-text index. Tool outputs are
#: unbounded (a ``read_file`` result can be a megabyte), and an index that
#: grew with them would cost more than the transcript itself. The cap is a
#: *search* budget, never a truncation of stored content: ``Item.content``
#: keeps the whole payload, only the indexed projection is clipped.
SEARCH_TEXT_MAX_LEN = 8000

#: Longest contribution from any single ``content`` field, so one giant
#: tool output cannot crowd every other field out of the budget above.
_SEARCH_FIELD_MAX_LEN = 2000

#: ``Item.content`` keys whose values are indexed for full-text search, in
#: concatenation order. Keys are listed explicitly rather than "index every
#: string in the record" because ``content`` is an open record: indexing it
#: wholesale would silently start indexing ids, base64 blobs and internal
#: bookkeeping the moment a tool grew a field.
_SEARCHABLE_CONTENT_KEYS = (
    "title",
    "text",
    "summary",
    "message",
    "tool",
    "server_label",
    "input",
    "arguments",
    "output",
    "value",
    "reason",
    "rejection_message",
)


def _search_fragment(value: object) -> str:
    """One ``content`` value flattened to indexable text, length-capped."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, (int, float)):
        text = str(value)
    else:
        try:
            text = json.dumps(value, default=str)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            text = str(value)
    text = " ".join(text.split())
    return text[:_SEARCH_FIELD_MAX_LEN]


#: ``content`` keys that are NEVER indexed, per item kind.
#:
#: An elicitation's ``value`` is the user's ANSWER to a server-raised
#: prompt, and a session-scoped MCP server may raise arbitrary form
#: elicitations — "paste your API key" is a supported flow. Indexing it
#: makes the credential a search term and, worse, puts it verbatim into the
#: preview snippet a listing renders. This codebase's posture is that
#: secret-ish material is write-only, so the answer never reaches the
#: index. The prompt (``message``/``title``) still does: that is what makes
#: the elicitation findable, and it is the server's own text.
_UNINDEXED_CONTENT_KEYS: Dict[str, frozenset] = {
    "elicitation": frozenset({"value"}),
}


def derive_search_text(kind: "ItemKind | str", content: Dict[str, Any]) -> str:
    """The indexable projection of one item's ``content``.

    Deterministic and pure, so the SQLite and Postgres backends index the
    *same* bytes for the same item and a search that matches on one backend
    matches on the other. Both stores call this at their single item-write
    site; neither has a second derivation of its own.

    ``kind`` selects the per-kind exclusions in
    :data:`_UNINDEXED_CONTENT_KEYS`.
    """
    if not isinstance(content, dict):
        return ""
    kind_value = kind.value if isinstance(kind, ItemKind) else str(kind)
    excluded = _UNINDEXED_CONTENT_KEYS.get(kind_value, frozenset())
    parts: List[str] = []
    budget = SEARCH_TEXT_MAX_LEN
    for key in _SEARCHABLE_CONTENT_KEYS:
        if budget <= 0:
            break
        if key in excluded:
            continue
        fragment = _search_fragment(content.get(key))
        if not fragment:
            continue
        parts.append(fragment[:budget])
        budget -= len(fragment) + 1
    return " ".join(parts)[:SEARCH_TEXT_MAX_LEN]


def search_terms(query: str) -> List[str]:
    """Split a user query into the terms both backends AND together.

    Two jobs, and the second one is not obvious.

    **Safety.** The query is treated as a bag of terms, never as backend
    query syntax: an FTS5 ``MATCH`` expression and a Postgres ``tsquery``
    both have operators that a user typing a quote or a ``*`` would trip
    over, and a search box that raises a parse error on an apostrophe is
    broken. Terms are recombined by each backend using its own quoting, so
    nothing a user types can reach the parser.

    **Cross-backend agreement.** The split is on every non-alphanumeric
    character, which is exactly what SQLite's ``unicode61`` tokenizer does
    to the indexed text. Postgres' parser does NOT: it keeps
    ``widgets.py`` as one ``file`` lexeme, so a search for ``widgets``
    matched on SQLite and missed on Postgres. Splitting the query the same
    way the index splits the document — and normalizing the Postgres
    tsvector to match (see ``_PG_SCHEMA_SQL``) — is what makes one query
    return one answer on both.
    """
    if not isinstance(query, str):
        return []
    terms: List[str] = []
    current: List[str] = []
    for char in query:
        if char.isalnum():
            current.append(char)
        elif current:
            terms.append("".join(current))
            current = []
    if current:
        terms.append("".join(current))
    return terms


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


def encode_thread_cursor(sort_value: float, thread_id: str) -> str:
    """Cursor for the thread listing's ``(sort_value, thread_id)`` keyset.

    A thread listing is ordered by a timestamp, which is **not unique** —
    two threads created in the same millisecond, and every legacy thread
    (projected rows carry ``0.0``, documented in protocol.md), collide. A
    keyset on the timestamp alone would therefore either skip or repeat
    those rows at a page boundary. The thread id is the tiebreaker that
    makes the ordering total, which is what makes the keyset sound.
    """
    return encode_cursor({"sort": float(sort_value), "id": str(thread_id)})


def decode_thread_cursor(cursor: str) -> tuple[float, str]:
    """Inverse of :func:`encode_thread_cursor`. Raises :class:`CursorError`."""
    decoded = decode_cursor(cursor)
    sort_value = decoded.get("sort")
    thread_id = decoded.get("id")
    if isinstance(sort_value, bool) or not isinstance(sort_value, (int, float)):
        raise CursorError(f"Malformed thread cursor: {cursor!r}")
    if not isinstance(thread_id, str) or not thread_id:
        raise CursorError(f"Malformed thread cursor: {cursor!r}")
    return float(sort_value), thread_id


class _Unscoped:
    """Sentinel: this listing deliberately spans every principal.

    Distinct from a ``user_id`` string, and deliberately NOT ``None``. See
    :class:`ThreadFilter` for why the scope has no default at all: an
    omitted scope must not be expressible as an unscoped one.
    """

    _instance: "_Unscoped | None" = None

    def __new__(cls) -> "_Unscoped":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNSCOPED"


#: The only value that widens a thread listing past one principal. Legal
#: in exactly two situations, both of which the RPC layer resolves before
#: it builds a filter: authorization is not enforced (single-user), or the
#: caller holds the ``admin`` role.
UNSCOPED = _Unscoped()


T = TypeVar("T")


@dataclass(frozen=True)
class Page(Generic[T]):
    """One page of a keyset-paginated read."""

    entries: Sequence[T]
    next_cursor: Optional[str]
    has_more: bool


@dataclass(frozen=True)
class ThreadFilter:
    """The predicate a thread listing is drawn under.

    One object rather than eleven keyword arguments threaded through the
    service, both stores and the RPC layer: the listing and its ``total``
    MUST be computed under the identical predicate, and passing a single
    value to both is how that stays true when a filter is added.

    ``user_id`` is the authorization scope and is applied **in SQL**, next
    to ``LIMIT``. Filtering owned rows out of an already-paginated result
    in Python silently under-delivers pages — the defect review found in
    ``list_sessions`` (PR #294) — which is why it is part of the object the
    store methods take whole rather than a separate argument.

    It has **no default**, and "everything" is spelled :data:`UNSCOPED`
    rather than ``None``. An optional scope defaulting to "no filter" is
    fail-OPEN: ``ThreadFilter()`` would have been a legal, fully unscoped
    predicate, so forgetting the scope and deliberately asking for every
    principal's threads were the same expression and no reviewer could tell
    them apart. This is the posture ``rpc.auth.OWNER_UNKNOWN`` exists to
    hold on the request side; the same rule applies here.
    """

    user_id: "str | _Unscoped"
    status: Optional["ThreadStatus"] = None
    pinned: Optional[bool] = None
    source: Optional[str] = None
    model: Optional[str] = None
    parent_thread_id: Optional[str] = None
    created_after: Optional[float] = None
    created_before: Optional[float] = None
    updated_after: Optional[float] = None
    updated_before: Optional[float] = None


@dataclass(frozen=True)
class SearchHit:
    """One thread that matched a full-text query, with its best preview."""

    thread_id: str
    match_count: int
    #: ``item`` when the best match was a transcript item, ``thread_title``
    #: when it was the conversation's title.
    match_kind: str
    item_id: Optional[str]
    turn_id: Optional[str]
    seq: Optional[int]
    #: Backend-generated snippet around the match — SQLite ``snippet()``,
    #: Postgres ``ts_headline()``. Plain text, no markup.
    preview: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "match_count": self.match_count,
            "match_kind": self.match_kind,
            "item_id": self.item_id,
            "turn_id": self.turn_id,
            "seq": self.seq,
            "preview": self.preview,
        }


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
    #: Client-set "keep this at the top" flag. Organization state, not
    #: lifecycle state: a pinned thread is otherwise an ordinary active or
    #: archived thread, which is why this is a separate field rather than a
    #: third :class:`ThreadStatus` value — the two axes are independent and
    #: a user can pin an archived conversation.
    pinned: bool = False
    #: Open record of client-owned organization metadata (labels, colors,
    #: a workspace hint). Deliberately unvalidated and never interpreted by
    #: the server: the alternative is a protocol break every time a client
    #: wants to remember one more thing about a conversation.
    metadata: Dict[str, Any] = field(default_factory=dict)
    #: ``"worker"`` (delegated work), ``"recorded"`` (ordinary live chats) or ``"adapter"``
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
            "pinned": bool(self.pinned),
            "metadata": dict(self.metadata),
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
