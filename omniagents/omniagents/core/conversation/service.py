"""Read path: paginated Thread / Turn / Item reads with canonical ids.

The contract this module exists to keep: **a completed conversation is
reconstructible from persisted domain objects alone.** No event-journal
replay, no re-deriving state from the notification stream, no reading the
raw history rows at render time. Reads hit the domain store and nothing
else — with one bounded exception, the compatibility path below.

Compatibility. A thread with no recorded rows is backfilled from legacy
history on first read (:mod:`~omniagents.core.conversation.adapter`),
once, and then served from the store like any other. The backfill is the
migration: it is idempotent, it refuses to touch a recorded thread, and it
runs lazily so no deployment needs a downtime window to adopt the model.

Pagination is keyset, not offset. ``list_items`` walks ``seq`` and
``list_turns`` walks ``ordinal``; the cursor carries that position, so a
thread being written while a client pages through it neither skips nor
repeats rows. Offsets cannot promise that, which matters exactly when the
history is large — the case pagination exists for.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from .adapter import backfill_thread
from .model import (
    CursorError,
    Item,
    ItemKind,
    Page,
    Thread,
    ThreadFilter,
    ThreadStatus,
    TurnStatus,
    decode_cursor,
    decode_thread_cursor,
    derive_title,
    encode_cursor,
    encode_thread_cursor,
)
from .store import (
    ConversationStore,
    SearchUnavailable,
    get_conversation_store,
)
from .plans import PlanStore
from .store import ConversationStore, get_conversation_store

logger = logging.getLogger(__name__)

__all__ = [
    "CascadeResult",
    "ConversationRpcError",
    "ConversationService",
    "DescendantNode",
    "ForkPointInvalid",
    "InvalidCursor",
    "ItemNotFound",
    "MAX_DESCENDANT_DEPTH",
    "MAX_PAGE_SIZE",
    "SearchUnsupported",
    "MAX_LISTED_DESCENDANTS",
    "METADATA_MAX_BYTES",
    "METADATA_MAX_KEYS",
    "ThreadHasDescendants",
    "ThreadMetadataInvalid",
    "ThreadNotFound",
    "ThreadOnLegalHold",
    "UNSET",
    "TurnNotFound",
]

#: JSON-RPC error codes. ``-32010..13``, ``-32020``, ``-32030``,
#: ``-32040..42``, ``-32050..52``, ``-32060..63``, ``-32070..76``,
#: ``-32080..82`` and ``-32090..95`` are taken by earlier surfaces; the
#: plan/diff surface opens the ``-321xx`` block.
RPC_ERROR_THREAD_NOT_FOUND = -32080
RPC_ERROR_ITEM_NOT_FOUND = -32081
RPC_ERROR_CURSOR_INVALID = -32082
RPC_ERROR_TURN_NOT_FOUND = -32100

#: Conversation organization & branching (tkt_eZldW-Gje6kn). ``-3209x`` is
#: taken by the git surface and ``-3210x`` by the durable-plan work, so this
#: block opens at ``-32110``.
RPC_ERROR_SEARCH_UNAVAILABLE = -32110
RPC_ERROR_THREAD_HAS_DESCENDANTS = -32111
RPC_ERROR_FORK_POINT_INVALID = -32112
RPC_ERROR_THREAD_ON_LEGAL_HOLD = -32113
RPC_ERROR_THREAD_METADATA_INVALID = -32114

#: Hard ceiling on a page. A client asking for more gets ``MAX_PAGE_SIZE``
#: plus a cursor, never a 200k-item result that stalls the socket.
MAX_PAGE_SIZE = 500
DEFAULT_PAGE_SIZE = 50

#: Ceiling on how deep an ancestry walk descends. A fork chain is a user
#: action; anything past this is a runaway, and the read must stay bounded
#: rather than become the slowest call in the protocol.
MAX_DESCENDANT_DEPTH = 32

#: Ceiling on how many descendants one ancestry read returns.
MAX_DESCENDANTS = 500

#: Page size for the two branch-point scans. They walk with the ordinary
#: keyset, so this bounds memory per round trip, not the answer.
_SEGMENT_SCAN = 500

#: Item kinds that put at least one row into the raw history. Approvals,
#: elicitations and artifacts do not, so they never block a cut.
_HISTORY_BEARING_KINDS = frozenset(
    {
        ItemKind.USER_MESSAGE,
        ItemKind.AGENT_MESSAGE,
        ItemKind.REASONING,
        ItemKind.TOOL_CALL,
        ItemKind.COMPACTION,
    }
)

#: Ceiling on how many descendant ids a typed ERROR enumerates. The count
#: it reports alongside is exact and unbounded; only the list is clipped,
#: so an error payload cannot itself become a megabyte.
MAX_LISTED_DESCENDANTS = 100

#: ``Thread.metadata`` is client-owned and never interpreted by the server,
#: but it is persisted and fanned out on every ``thread_updated``. Unbounded
#: it is a storage and broadcast amplifier, so it is capped the way
#: ``Thread.title`` is — by refusing, not by truncating: silently dropping
#: half a client's annotation is data loss.
METADATA_MAX_KEYS = 64
METADATA_MAX_BYTES = 16_384


@dataclass(frozen=True)
class DescendantNode:
    """One thread below a root, as the single descendant walk reports it.

    Carries the owner because ownership is decided **per node**: the walk
    itself answers "does this relationship exist", and every caller — the
    ancestry read, the archive cascade, the delete guard — turns
    ``user_id`` into its own access decision over the same rows.
    """

    thread_id: str
    user_id: Optional[str]
    depth: int


@dataclass(frozen=True)
class CascadeResult:
    """What a status cascade actually did.

    ``skipped`` is a COUNT, never a list: the threads it counts are ones
    the caller may not read, and a cascade report is not a side channel for
    their ids.
    """

    moved: List[str]
    skipped: int


class _Unset:
    """Sentinel: "this field was not supplied".

    ``update_thread`` has to tell "leave the title alone" from "clear the
    title", and both arrive as the absence/presence of a JSON ``null``. The
    RPC layer dumps validated params with ``exclude_unset=True``, so an
    omitted field never reaches the handler and this default survives,
    while an explicit ``null`` arrives as ``None``.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = _Unset()


class ConversationRpcError(Exception):
    """Typed conversation error, surfaced verbatim by ``JsonRpcEndpoint``."""

    code: int = -32603
    kind: str = "conversation_error"

    def __init__(self, message: str, **data: Any) -> None:
        super().__init__(message)
        self._data = {key: value for key, value in data.items() if value is not None}

    @property
    def data(self) -> Dict[str, Any]:
        return {"kind": self.kind, **self._data}


class ThreadNotFound(ConversationRpcError):
    code = RPC_ERROR_THREAD_NOT_FOUND
    kind = "thread_not_found"

    def __init__(self, thread_id: str) -> None:
        super().__init__(f"Unknown thread: {thread_id}", thread_id=thread_id)


class ItemNotFound(ConversationRpcError):
    code = RPC_ERROR_ITEM_NOT_FOUND
    kind = "item_not_found"

    def __init__(self, thread_id: str, item_id: str) -> None:
        super().__init__(
            f"Unknown item {item_id} in thread {thread_id}",
            thread_id=thread_id,
            item_id=item_id,
        )


class TurnNotFound(ConversationRpcError):
    code = RPC_ERROR_TURN_NOT_FOUND
    kind = "turn_not_found"

    def __init__(self, thread_id: str, turn_id: str) -> None:
        super().__init__(
            f"Unknown turn {turn_id} in thread {thread_id}",
            thread_id=thread_id,
            turn_id=turn_id,
        )


class InvalidCursor(ConversationRpcError):
    code = RPC_ERROR_CURSOR_INVALID
    kind = "cursor_invalid"

    def __init__(self, cursor: str) -> None:
        super().__init__("Malformed pagination cursor", cursor=cursor)


class SearchUnsupported(ConversationRpcError):
    """The deployment's storage has no full-text index."""

    code = RPC_ERROR_SEARCH_UNAVAILABLE
    kind = "search_unavailable"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)


class ThreadHasDescendants(ConversationRpcError):
    """A destructive operation would orphan forks of this thread."""

    code = RPC_ERROR_THREAD_HAS_DESCENDANTS
    kind = "thread_has_descendants"

    def __init__(self, thread_id: str, total: int, visible: Sequence[str]) -> None:
        listed = list(visible)[:MAX_LISTED_DESCENDANTS]
        # ``truncated`` is true when the LIST is not the whole story, for
        # either reason: the cap clipped it, or some descendants belong to
        # a principal the caller may not read and are counted only.
        truncated = total > len(listed)
        super().__init__(
            f"Thread {thread_id} has {total} descendant thread(s); "
            "delete them first or retry with cascade=true",
            thread_id=thread_id,
            # Only ids the caller may READ. The guard walk is deliberately
            # owner-agnostic so it cannot miss a descendant, but that must
            # not turn a refusal into a side channel for the ids of threads
            # ``list_thread_descendants`` hides from this caller.
            descendant_thread_ids=listed,
            # The COUNT is always exact — the guard walk is unbounded. Only
            # the id LIST is capped/filtered, and it says so, so a client
            # can never read a partial enumeration as a complete one.
            descendant_count=total,
            truncated=truncated,
        )


class ThreadOnLegalHold(ConversationRpcError):
    """A destructive operation would touch a session under legal hold.

    Raised BEFORE anything is deleted. The hold check used to live inside
    the history backend's ``delete_session``, which a cascade only reaches
    after it has already removed the subtree — so a held parent produced a
    failed call and permanently deleted branches. A hold that only takes
    effect once the branches are gone is not a hold.
    """

    code = RPC_ERROR_THREAD_ON_LEGAL_HOLD
    kind = "thread_on_legal_hold"

    def __init__(self, thread_id: str, held: Sequence[str]) -> None:
        super().__init__(
            f"Cannot delete {thread_id}: "
            f"{len(held)} session(s) in this subtree are under legal hold",
            thread_id=thread_id,
            held_thread_ids=list(held)[:MAX_LISTED_DESCENDANTS],
            held_count=len(held),
        )


class ThreadMetadataInvalid(ConversationRpcError):
    """A metadata update exceeded the per-thread caps."""

    code = RPC_ERROR_THREAD_METADATA_INVALID
    kind = "thread_metadata_invalid"

    def __init__(self, reason: str, **data: Any) -> None:
        super().__init__(reason, **data)


class ForkPointInvalid(ConversationRpcError):
    """The requested branch point cannot be forked from."""

    code = RPC_ERROR_FORK_POINT_INVALID
    kind = "fork_point_invalid"

    def __init__(self, message: str, **data: Any) -> None:
        super().__init__(message, **data)


def _clamp(limit: Optional[int]) -> int:
    """Clamp a requested page size into ``[1, MAX_PAGE_SIZE]``.

    Clamping rather than rejecting: ``limit=0`` from a client computing a
    window size should return one row, not a typed error mid-scroll. The
    behaviour is documented in protocol.md so it is a contract, not a
    surprise."""
    if limit is None:
        return DEFAULT_PAGE_SIZE
    return max(1, min(int(limit), MAX_PAGE_SIZE))


def _now() -> float:
    return time.time()


def _validate_metadata(metadata: Dict[str, Any]) -> None:
    """Enforce the per-thread metadata caps on the MERGED record.

    Checked after the merge, not on the delta, because the persisted and
    broadcast object is the merged one — a client could otherwise grow a
    thread past any cap one small update at a time.
    """
    if len(metadata) > METADATA_MAX_KEYS:
        raise ThreadMetadataInvalid(
            f"Thread metadata may hold at most {METADATA_MAX_KEYS} keys "
            f"(this update would leave {len(metadata)})",
            keys=len(metadata),
            max_keys=METADATA_MAX_KEYS,
        )
    try:
        encoded = json.dumps(metadata, default=str).encode("utf-8")
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise ThreadMetadataInvalid("Thread metadata must be JSON-encodable") from exc
    if len(encoded) > METADATA_MAX_BYTES:
        raise ThreadMetadataInvalid(
            f"Thread metadata may be at most {METADATA_MAX_BYTES} bytes "
            f"(this update would leave {len(encoded)})",
            size=len(encoded),
            max_size=METADATA_MAX_BYTES,
        )


def _thread_cursor_position(cursor: Optional[str]) -> Optional[tuple[float, str]]:
    if not cursor:
        return None
    try:
        return decode_thread_cursor(cursor)
    except CursorError as exc:
        raise InvalidCursor(cursor) from exc


def _search_cursor_offset(cursor: Optional[str]) -> int:
    offset = _cursor_position(cursor, "offset")
    return offset or 0


def _cursor_position(cursor: Optional[str], field: str) -> Optional[int]:
    if not cursor:
        return None
    try:
        decoded = decode_cursor(cursor)
    except CursorError as exc:
        raise InvalidCursor(cursor) from exc
    value = decoded.get(field)
    # ``bool`` is an ``int`` subclass; a boolean cursor is malformed.
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InvalidCursor(cursor)
    return value


class ConversationService:
    """Read API over one (project, agent) shard's domain store."""

    def __init__(
        self,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
        store: ConversationStore | None = None,
    ) -> None:
        self._project_slug = project_slug
        self._agent_slug = agent_slug
        self._store = store

    @property
    def store(self) -> ConversationStore:
        if self._store is None:
            self._store = get_conversation_store(self._project_slug, self._agent_slug)
        return self._store

    # -- compatibility ---------------------------------------------------

    def _load_legacy_history(self, thread_id: str) -> List[Dict[str, Any]]:
        from omniagents.core.session.history_db import load_history

        rows = load_history(
            thread_id,
            project_slug=self._project_slug,
            agent_slug=self._agent_slug,
        )
        return [row for row in rows if isinstance(row, dict)]

    def _legacy_metadata(self, thread_id: str) -> Dict[str, Any]:
        from omniagents.core.session.history_db import get_archived, get_session_user_id

        metadata: Dict[str, Any] = {"user_id": None, "archived": False}
        try:
            metadata["user_id"] = get_session_user_id(
                thread_id,
                project_slug=self._project_slug,
                agent_slug=self._agent_slug,
            )
        except Exception:
            logger.debug("legacy owner lookup failed for %s", thread_id)
        try:
            metadata["archived"] = bool(
                get_archived(
                    thread_id,
                    project_slug=self._project_slug,
                    agent_slug=self._agent_slug,
                )
            )
        except Exception:
            logger.debug("legacy archive lookup failed for %s", thread_id)
        return metadata

    def ensure_thread(self, thread_id: str) -> Thread:
        """Resolve a thread, materializing a legacy one on first sight.

        **Reads never rewrite an existing thread.** The guard is existence,
        not provenance: if the row is there, the recorder (or an earlier
        projection) already owns it and re-projecting would overwrite live
        rows and reset the ordinal cursor — which silently destroyed items.
        A thread that has *no* row has never been touched by this model, so
        projecting it here is the one safe moment.

        The complementary half of the invariant lives in
        :meth:`ConversationRecorder.ensure_thread`, which projects before it
        creates a row. Between them: a row exists ⇒ its prior history has
        been projected exactly once.
        """
        thread = self.store.get_thread(thread_id)
        if thread is not None:
            return thread
        history = self._load_legacy_history(thread_id)
        if history:
            metadata = self._legacy_metadata(thread_id)
            backfilled = backfill_thread(
                self.store,
                thread_id,
                history,
                user_id=metadata["user_id"],
                archived=metadata["archived"],
            )
            if backfilled is not None:
                return backfilled
        raise ThreadNotFound(thread_id)

    # -- reads -----------------------------------------------------------

    def get_thread(self, thread_id: str) -> Dict[str, Any]:
        thread = self.ensure_thread(thread_id)
        payload = thread.to_dict()
        # The stored counters are maintained by non-transactional
        # read-modify-write and can drift under concurrent turns. They are
        # a bookkeeping convenience, not a source of truth, so the WIRE
        # value is counted at read time — a client sizing a scrollbar from
        # ``item_count`` must not be handed a stale number.
        payload["item_count"] = self.store.count_items(thread_id)
        payload["turn_count"] = self.store.count_turns(thread_id)
        return payload

    def list_turns(
        self,
        thread_id: str,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        descending: bool = False,
    ) -> Dict[str, Any]:
        self.ensure_thread(thread_id)
        size = _clamp(limit)
        position = _cursor_position(cursor, "ordinal")
        rows = self.store.list_turns(
            thread_id,
            limit=size + 1,
            after_ordinal=None if descending else position,
            before_ordinal=position if descending else None,
            descending=descending,
        )
        page = _to_page(rows, size, lambda turn: {"ordinal": turn.ordinal})
        turns: List[Dict[str, Any]] = []
        for turn in page.entries:
            payload = turn.to_dict()
            payload["item_count"] = self.store.count_items(
                thread_id, turn_id=turn.turn_id
            )
            turns.append(payload)
        return {
            "thread_id": thread_id,
            "turns": turns,
            "next_cursor": page.next_cursor,
            "has_more": page.has_more,
            "total": self.store.count_turns(thread_id),
        }

    def list_items(
        self,
        thread_id: str,
        *,
        turn_id: str | None = None,
        kinds: Sequence[str] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        descending: bool = False,
    ) -> Dict[str, Any]:
        self.ensure_thread(thread_id)
        size = _clamp(limit)
        position = _cursor_position(cursor, "seq")
        parsed_kinds = _parse_kinds(kinds)
        if kinds and not parsed_kinds:
            return {
                "thread_id": thread_id,
                "turn_id": turn_id,
                "items": [],
                "next_cursor": None,
                "has_more": False,
                "total": 0,
            }
        rows = self.store.list_items(
            thread_id,
            limit=size + 1,
            turn_id=turn_id,
            kinds=parsed_kinds,
            after_seq=None if descending else position,
            before_seq=position if descending else None,
            descending=descending,
        )
        page = _to_page(rows, size, lambda item: {"seq": item.seq})
        return {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "items": [item.to_dict() for item in page.entries],
            "next_cursor": page.next_cursor,
            "has_more": page.has_more,
            # Counted under the SAME predicate the page was drawn with,
            # filter included — a `total` that ignored `kinds` reported 21
            # next to a single returned item.
            "total": self.store.count_items(
                thread_id, turn_id=turn_id, kinds=parsed_kinds
            ),
        }

    def get_item(self, thread_id: str, item_id: str) -> Dict[str, Any]:
        self.ensure_thread(thread_id)
        item = self.store.get_item(thread_id, item_id)
        if item is None:
            raise ItemNotFound(thread_id, item_id)
        return item.to_dict()

    # -- organization: listing, search, ancestry -------------------------

    def _with_exact_counts(self, threads: List[Thread]) -> List[Dict[str, Any]]:
        """Thread payloads whose counters are counted, not remembered.

        The same rule ``get_thread`` applies, at page scale: the stored
        counters are maintained by non-transactional read-modify-write and
        drift under concurrent turns, and a listing is exactly the surface
        a client sizes its UI from. Two grouped statements per page, not
        two per row.
        """
        ids = [thread.thread_id for thread in threads]
        item_counts = self.store.count_items_for_threads(ids)
        turn_counts = self.store.count_turns_for_threads(ids)
        payloads: List[Dict[str, Any]] = []
        for thread in threads:
            payload = thread.to_dict()
            payload["item_count"] = item_counts.get(thread.thread_id, 0)
            payload["turn_count"] = turn_counts.get(thread.thread_id, 0)
            payloads.append(payload)
        return payloads

    def list_threads(
        self,
        *,
        thread_filter: ThreadFilter,
        limit: int | None = None,
        cursor: str | None = None,
        descending: bool = True,
    ) -> Dict[str, Any]:
        """Keyset page of threads under *thread_filter*, newest first.

        ``thread_filter`` is passed to the store whole. The ownership scope
        lives inside it precisely so it cannot be applied "afterwards": a
        listing that pages first and filters second under-delivers, which is
        the bug review found in ``list_sessions`` (PR #294).
        """
        size = _clamp(limit)
        position = _thread_cursor_position(cursor)
        rows = self.store.list_threads(
            thread_filter, limit=size + 1, after=position, descending=descending
        )
        has_more = len(rows) > size
        entries = rows[:size]
        next_cursor = (
            encode_thread_cursor(entries[-1].updated_at, entries[-1].thread_id)
            if has_more and entries
            else None
        )
        return {
            "threads": self._with_exact_counts(list(entries)),
            "next_cursor": next_cursor,
            "has_more": has_more,
            "total": self.store.count_threads(thread_filter),
        }

    def search_threads(
        self,
        query: str,
        *,
        thread_filter: ThreadFilter,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Dict[str, Any]:
        """Full-text search across titles and transcript content.

        Paged by offset rather than by a keyset: results come back in rank
        order, and rank is not a column a cursor can resume from. The
        offset is carried inside the same opaque cursor token the rest of
        the surface uses, so a client's paging loop is identical.
        """
        size = _clamp(limit)
        offset = _search_cursor_offset(cursor)
        try:
            hits, total = self.store.search(
                query, thread_filter, limit=size + 1, offset=offset
            )
        except SearchUnavailable as exc:
            raise SearchUnsupported(str(exc)) from exc
        has_more = len(hits) > size
        entries = hits[:size]
        results: List[Dict[str, Any]] = []
        # The thread row is what the predicate matched on, so it exists
        # unless it was deleted between the two statements. Skipping a
        # vanished row is more honest than emitting a half-populated result
        # a client would render as an empty conversation.
        threads = [
            thread
            for thread in (self.store.get_thread(hit.thread_id) for hit in entries)
            if thread is not None
        ]
        by_id = {
            payload["thread_id"]: payload
            for payload in self._with_exact_counts(threads)
        }
        for hit in entries:
            thread_payload = by_id.get(hit.thread_id)
            if thread_payload is None:  # pragma: no cover - concurrent delete
                continue
            payload = hit.to_dict()
            payload["thread"] = thread_payload
            results.append(payload)
        return {
            "query": query,
            "results": results,
            "next_cursor": (
                encode_cursor({"offset": offset + len(entries)}) if has_more else None
            ),
            "has_more": has_more,
            "total": total,
        }

    def list_descendants(
        self,
        thread_id: str,
        *,
        can_access: "Callable[[Optional[str]], bool]",
        max_depth: int | None = None,
        limit: int | None = None,
    ) -> Dict[str, Any]:
        """The fork subtree below *thread_id*, plus its own ancestry links.

        Both directions in one read: a client rendering a branch tree needs
        to know where the thread it is looking at came FROM as well as what
        came after it, and two round trips to draw one widget is how
        multi-column navigation ends up inconsistent between columns.

        **Ownership is per node, not per path** — see
        :meth:`descendant_nodes`. ``can_access`` has no default, for the
        same reason the cascade's has none: an unscoped ancestry read is
        fail-OPEN, and "forgot the scope" and "deliberately unscoped" must
        not be the same expression.

        ``truncated`` covers BOTH clips. The cascade this read is supposed
        to predict is uncapped, so a depth clip that did not raise the flag
        reproduced the very defect the per-node rule fixed, one axis over:
        the read shows 32 of a 40-deep chain, the cascade archives all 40,
        and nothing in the payload says the client is not seeing everything
        that is about to move. A descendant the caller may not access is
        NOT a clip — it is never shown to anybody and the cascade never
        touches it.
        """
        thread = self.ensure_thread(thread_id)
        depth = max(
            1, min(int(max_depth or MAX_DESCENDANT_DEPTH), MAX_DESCENDANT_DEPTH)
        )
        count = max(1, min(int(limit or MAX_DESCENDANTS), MAX_DESCENDANTS))
        # Uncapped on purpose: ``truncated`` is a statement about what was
        # withheld, and it cannot be computed from an already-clipped list.
        accessible = self.accessible_descendants(thread_id, can_access=can_access)
        rows = [node for node in accessible if node.depth <= depth]
        page = rows[:count]
        truncated = len(accessible) > len(page)
        # One batched read for the page rather than one per row: the walk
        # above returns ids, and the ancestry read is a listing surface.
        by_id = self.store.get_threads([node.thread_id for node in page])
        present = [
            (node, by_id[node.thread_id]) for node in page if node.thread_id in by_id
        ]
        payloads = self._with_exact_counts([thread_row for _, thread_row in present])
        descendants = [
            {**payload, "depth": node.depth}
            for payload, (node, _) in zip(payloads, present)
        ]
        return {
            "thread_id": thread_id,
            "parent_thread_id": thread.parent_thread_id,
            "branched_from_item_id": thread.branched_from_item_id,
            "descendants": descendants,
            "max_depth": depth,
            "truncated": truncated,
        }

    def export_thread(
        self,
        thread_id: str,
        *,
        can_access: "Callable[[Optional[str]], bool]",
        limit: int | None = None,
        cursor: str | None = None,
        include_descendants: bool = False,
    ) -> Dict[str, Any]:
        """One page of a thread's canonical objects, for archival export.

        Exports the DOMAIN objects, not the raw history, for the same
        reason a fork copies them: approvals, elicitations and artifacts
        never reach the history table, so a history-based export silently
        drops the parts of a conversation a user is most likely to need to
        produce later.

        **The ITEMS are never capped**: iterating ``next_cursor`` until it
        is null yields every one. An export that clipped them and set a
        flag would be exactly the "leaves transcript behind" failure this
        surface exists to avoid.

        Turn HEADERS are a convenience rather than the transcript, and they
        ship whole on the first page so a consumer can rebuild turn
        structure without a second paging loop. That convenience has a
        ceiling (``MAX_PAGE_SIZE``), and when a thread exceeds it
        ``turns_truncated`` says so rather than the page quietly dropping
        headers — ``list_turns`` pages the rest. Saying "no silent cap" and
        then silently capping one of the three lists is the kind of gap
        this surface is supposed to be free of.

        ``include_descendants`` names the same nodes the ancestry read
        shows, under the same per-node ``can_access`` — which is why the
        predicate is required here rather than optional.
        """
        thread = self.ensure_thread(thread_id)
        size = _clamp(limit)
        position = _cursor_position(cursor, "seq")
        rows = self.store.list_items(thread_id, limit=size + 1, after_seq=position)
        page = _to_page(rows, size, lambda item: {"seq": item.seq})
        turns = (
            self.store.list_turns(thread_id, limit=MAX_PAGE_SIZE)
            if position is None
            else []
        )
        payload: Dict[str, Any] = {
            "thread": self.get_thread(thread_id),
            "turns": [turn.to_dict() for turn in turns],
            "turns_truncated": (
                position is None and self.store.count_turns(thread_id) > len(turns)
            ),
            "items": [item.to_dict() for item in page.entries],
            "next_cursor": page.next_cursor,
            "has_more": page.has_more,
        }
        if include_descendants:
            # The SAME walk and the SAME per-node scoping the ancestry read
            # uses: an export whose id list disagreed with the branch tree
            # the client just drew is the divergence this method would
            # otherwise reintroduce from a third copy.
            payload["descendant_thread_ids"] = [
                node.thread_id
                for node in self.accessible_descendants(
                    thread_id, can_access=can_access, max_depth=MAX_DESCENDANT_DEPTH
                )[:MAX_DESCENDANTS]
            ]
        else:
            payload["descendant_thread_ids"] = []
        _ = thread
        return payload

    def descendant_nodes(self, thread_id: str) -> List[DescendantNode]:
        """**The** descendant walk. Every other one is a view of this.

        One walk backs three surfaces that must describe the same tree —
        the ancestry read (:meth:`list_descendants`), the archive/unarchive
        cascade (:meth:`set_status`) and the delete guard. They diverged
        once: the read required the caller's ownership at every HOP while
        the cascade checked it per NODE, so a thread of the caller's
        forked below somebody else's fork was invisible to the read and
        still came back in ``cascaded_thread_ids``.

        Two properties resolve that, and both live here rather than in any
        caller:

        * **Unbounded and owner-agnostic.** It answers "does this
          relationship exist", which is an integrity question. A capped
          guard reports "these are all the children" when it is not, and
          the caller then destroys the root and orphans the overflow. A
          caller that wants a bounded answer — the read — bounds its own
          OUTPUT and says ``truncated``.
        * **Ownership is per node.** ``user_id`` rides along with every id
          so each caller makes its own access decision, node by node.
          Ownership is a property of a thread, not of a path: a fork of a
          fork of your own thread is still yours, and a stranger forking
          in between is not a reason to hide it from you. A path-scoped
          rule protects nothing either — the foreign node itself is
          excluded by the per-node check either way; all the extra
          strictness hides is the caller's OWN threads.

        ``depth`` is 1 for a direct child and counts hops in the REAL
        tree, foreign nodes included, so two callers filtering the same
        rows differently still agree about shape.

        Ordered deepest FIRST, then by id: a caller deleting in this order
        never removes a parent before a child that still points at it, so
        an interrupted cascade leaves a shorter valid chain rather than a
        broken one.
        """
        rows = self.store.descendant_thread_rows(thread_id)
        parents = {child: parent for child, _, parent in rows}
        owners = {child: owner for child, owner, _ in rows}

        # Memoized: the walk under this is UNBOUNDED (the delete guard
        # needs it to be), so re-deriving each node's chain from scratch is
        # O(n^2) on a fork chain — a ``limit=5`` read on a 6000-node line
        # visited every node once per node. Each chain is walked at most
        # once and every node on it is resolved on the way back down.
        depths: Dict[str, int] = {}

        def depth_of(child: str) -> int:
            chain: List[str] = []
            on_chain: set[str] = set()
            cursor: Optional[str] = child
            base = 0
            while cursor is not None and cursor in parents:
                if cursor in depths:
                    base = depths[cursor]
                    break
                if cursor in on_chain:
                    # A parent_thread_id cycle the SQL de-duplication
                    # tolerated. Stop rather than spin; the numbers below a
                    # cycle are arbitrary either way, but they terminate.
                    break
                chain.append(cursor)
                on_chain.add(cursor)
                cursor = parents.get(cursor)
            for offset, node in enumerate(reversed(chain), start=1):
                depths[node] = base + offset
            return depths[child]

        nodes = [
            DescendantNode(
                thread_id=child, user_id=owners.get(child), depth=depth_of(child)
            )
            for child in parents
        ]
        nodes.sort(key=lambda node: (-node.depth, node.thread_id))
        return nodes

    def accessible_descendants(
        self,
        thread_id: str,
        *,
        can_access: "Callable[[Optional[str]], bool]",
        max_depth: int | None = None,
    ) -> List[DescendantNode]:
        """The nodes below *thread_id* the caller may see, shallowest first.

        The read-shaped view of :meth:`descendant_nodes`: same walk, same
        rows, per-node ``can_access``, ordered the way a branch tree is
        drawn.

        *max_depth* caps the OUTPUT — the walk beneath it stays unbounded,
        so a node the cap excludes is still descended through. ``None``
        means no ceiling, which is what a caller that has to REPORT its own
        clipping wants: it needs the full accessible set to know whether it
        withheld any of it.
        """
        visible = [
            node
            for node in self.descendant_nodes(thread_id)
            if (max_depth is None or node.depth <= max_depth)
            and can_access(node.user_id)
        ]
        visible.sort(key=lambda node: (node.depth, node.thread_id))
        return visible

    # -- organization: mutations -----------------------------------------

    def update_thread(
        self,
        thread_id: str,
        *,
        can_access: "Callable[[Optional[str]], bool]",
        title: Any = UNSET,
        pinned: bool | None = None,
        status: str | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Rename / pin / archive / annotate one thread.

        Returns ``{"thread", "changed", "cascaded_thread_ids",
        "inaccessible_descendant_count"}``. ``changed``
        lists only fields whose value actually moved, so a client can skip a
        re-render — and so a notification carries a real diff rather than
        "something happened".

        ``metadata`` MERGES: supplied keys are set, a key supplied as
        ``null`` is removed, unmentioned keys are untouched. Replace-whole
        semantics would make two clients editing different labels clobber
        each other, and the field exists for exactly that kind of
        concurrent, partial annotation.

        **Archive cascades to the fork subtree; nothing else does.** See
        :meth:`archive_thread`.
        """
        thread = self.ensure_thread(thread_id)
        changed: List[str] = []
        cascaded: List[str] = []
        inaccessible = 0

        if not isinstance(title, _Unset):
            new_title = derive_title(title) if isinstance(title, str) else None
            if new_title != thread.title:
                thread.title = new_title
                changed.append("title")
        if pinned is not None and bool(pinned) != thread.pinned:
            thread.pinned = bool(pinned)
            changed.append("pinned")
        if metadata is not None:
            merged = dict(thread.metadata)
            for key, value in metadata.items():
                if value is None:
                    merged.pop(key, None)
                else:
                    merged[key] = value
            _validate_metadata(merged)
            if merged != thread.metadata:
                thread.metadata = merged
                changed.append("metadata")

        target_status: ThreadStatus | None = None
        if status is not None:
            try:
                target_status = ThreadStatus(status)
            except ValueError as exc:
                raise ConversationRpcError(
                    f"Unknown thread status: {status}", status=status
                ) from exc

        if changed:
            thread.updated_at = _now()
            self.store.upsert_thread(thread)

        if target_status is not None and target_status is not thread.status:
            outcome = self.set_status(thread_id, target_status, can_access=can_access)
            cascaded = outcome.moved
            inaccessible = outcome.skipped
            changed.append("status")
            refreshed = self.store.get_thread(thread_id)
            if refreshed is not None:
                thread = refreshed

        return {
            "thread": self._with_exact_counts([thread])[0],
            "changed": changed,
            "cascaded_thread_ids": cascaded,
            # A COUNT, not ids: these are threads the caller may not read.
            "inaccessible_descendant_count": inaccessible,
        }

    def set_status(
        self,
        thread_id: str,
        status: ThreadStatus,
        *,
        can_access: "Callable[[Optional[str]], bool]",
    ) -> "CascadeResult":
        """Move a thread and its ACCESSIBLE fork subtree to *status*.

        **Descendant policy for archive/unarchive: CASCADE.** A fork is a
        branch of one line of work, so hiding the trunk while leaving its
        branches in the active list defeats the gesture entirely — the
        conversations a user was trying to get out of the way stay in view.
        Cascading is safe here in a way it is not for delete because the
        operation is *reversible*: the worst outcome is a branch that has
        to be unarchived again, not content that is gone. Unarchiving
        cascades for the same reason and with the documented cost that a
        branch archived on its own earlier comes back with its trunk.

        Archiving a leaf never touches its ancestors, so a client that
        wants per-branch control addresses the branch directly.

        **The cascade is scoped by ``can_access``, and the parameter has no
        default.** The walk it rides on
        (:meth:`descendant_nodes`) is deliberately owner-agnostic — it
        answers "does this relationship exist", which is an integrity
        question. Acting on those descendants is an ACCESS question, and
        the two must not share an answer: an unscoped cascade writes
        another principal's ``Thread.status``, their ``sessions.archived``
        row and their live ``Session.archived``, and it moves
        ``user_id IS NULL`` threads that ``principal_can_access`` denies to
        everyone. ``delete_session`` refuses outright in that situation;
        archive SKIPS instead, because it is reversible and routine —
        refusing would make a user unable to archive their own conversation
        because somebody else forked it.

        Returns the descendants that moved and a COUNT of the ones skipped.
        Only accessible ids are ever named: a caller who may not read a
        thread may not learn its id from a cascade report either.
        """
        moved: List[str] = []
        skipped = 0
        now = _now()

        def apply(target: str) -> bool:
            thread = self.store.get_thread(target)
            if thread is None or thread.status is status:
                return False
            thread.status = status
            thread.updated_at = now
            self.store.upsert_thread(thread)
            return True

        # The named thread's access was decided by the RPC gate before this
        # was called; the descendants' has not been decided anywhere yet.
        apply(thread_id)
        for node in self.descendant_nodes(thread_id):
            if not can_access(node.user_id):
                skipped += 1
                continue
            if apply(node.thread_id):
                moved.append(node.thread_id)
        return CascadeResult(moved=moved, skipped=skipped)

    # -- branching --------------------------------------------------------

    def resolve_branch_point(
        self,
        thread_id: str,
        *,
        from_item_id: str | None = None,
        from_turn_id: str | None = None,
    ) -> Optional[Item]:
        """The item a fork should branch after, or ``None`` for the tail.

        ``from_turn_id`` resolves to that turn's LAST item, so "branch from
        turn 3" means "the child ends where turn 3 ended" — the only reading
        that produces a child whose transcript is a prefix of the parent's.

        A turn that is still ``running`` is refused: its items are still
        landing, so the branch point it names is not yet a fixed position,
        and a fork taken there would copy an arbitrary prefix of a turn that
        is about to grow.
        """
        if from_item_id and from_turn_id:
            raise ForkPointInvalid(
                "Specify at most one of from_item_id / from_turn_id",
                thread_id=thread_id,
            )
        if from_item_id:
            item = self.store.get_item(thread_id, from_item_id)
            if item is None:
                raise ItemNotFound(thread_id, from_item_id)
            # Refuse a position the child's raw history cannot be cut at
            # exactly — see _assert_exact_history_cut.
            self._assert_exact_history_cut(thread_id, item)
            return item
        if from_turn_id:
            turn = self.store.get_turn(thread_id, from_turn_id)
            if turn is None:
                raise ForkPointInvalid(
                    f"Unknown turn {from_turn_id} in thread {thread_id}",
                    thread_id=thread_id,
                    turn_id=from_turn_id,
                )
            if turn.status is TurnStatus.RUNNING:
                raise ForkPointInvalid(
                    f"Turn {from_turn_id} is still running; fork from a "
                    "completed turn",
                    thread_id=thread_id,
                    turn_id=from_turn_id,
                    status=turn.status.value,
                )
            items = self.store.list_items(
                thread_id, limit=1, turn_id=from_turn_id, descending=True
            )
            if not items:
                raise ForkPointInvalid(
                    f"Turn {from_turn_id} holds no items to branch from",
                    thread_id=thread_id,
                    turn_id=from_turn_id,
                )
            return items[0]
        return None

    def _assert_exact_history_cut(self, thread_id: str, item: Item) -> None:
        """Refuse a branch point the child's raw history cannot be cut at.

        The child's raw history is what the model receives when it resumes,
        so it has to end where the child's transcript ends. The two
        sequences line up at exactly two kinds of position, and the unit is
        the **user row**, not the turn:

        * **At a user message.** One ``user_message`` item is one
          ``role: "user"`` history row, mid-run ones included, so "keep
          everything up to and including this question" is exactly
          expressible.
        * **At the tail of a user-message segment** — the last item before
          the next user message (or the end of the thread) that puts
          anything into history at all. Everything in the segment is kept,
          nothing after it is.

        Anywhere else the sequences do not line up: a single ``tool_call``
        item is TWO history rows (call + output), an ``approval`` or
        ``elicitation`` item is NONE, so "cut the history after item k" has
        no answer. Keeping the whole segment instead — which is what any
        turn-count arithmetic does — produces a child that already knows
        the answer it was forked to regenerate. Refusing is the honest
        outcome, and the error names the positions a client can retry at.

        A branch inside a RUNNING turn is refused separately: its items are
        still landing, so the position is not yet fixed.
        """
        if item.turn_id is not None:
            turn = self.store.get_turn(thread_id, item.turn_id)
            if turn is not None and turn.status is TurnStatus.RUNNING:
                raise ForkPointInvalid(
                    f"Turn {turn.turn_id} is still running; fork from a "
                    "completed turn",
                    thread_id=thread_id,
                    turn_id=turn.turn_id,
                    status=turn.status.value,
                )
        if item.kind is ItemKind.USER_MESSAGE:
            return
        segment_tail, blocking = self._segment_tail_after(thread_id, item.seq)
        if segment_tail:
            return
        raise ForkPointInvalid(
            "A branch point must be a user message or the last item before "
            "the next one; any other position has no exact cut in the "
            "model context",
            thread_id=thread_id,
            turn_id=item.turn_id,
            item_id=item.item_id,
            # What makes the cut inexact — the next item that puts a row
            # into raw history. A client needs both to explain the refusal
            # (an agent_message here means the following turn was opened by
            # an assistant-role wakeup) and to pick a reachable position.
            next_item_id=blocking.item_id if blocking is not None else None,
            next_item_kind=blocking.kind.value if blocking is not None else None,
        )

    def _segment_tail_after(
        self, thread_id: str, seq: int
    ) -> tuple[bool, Optional[Item]]:
        """Whether *seq* ends its user-message segment, and what blocks it.

        Items that put nothing into raw history — approvals, elicitations,
        artifacts — do not block: cutting before them loses no context.

        The blocker is returned whole, not just its id, because its KIND is
        what explains the refusal: an ``agent_message`` here means the next
        turn was opened by an assistant-role wakeup rather than a question.
        """
        cursor: Optional[int] = seq
        while True:
            rows = self.store.list_items(
                thread_id, limit=_SEGMENT_SCAN, after_seq=cursor
            )
            if not rows:
                return True, None
            for row in rows:
                if row.kind is ItemKind.USER_MESSAGE:
                    return True, None
                if row.kind in _HISTORY_BEARING_KINDS:
                    return False, row
            cursor = rows[-1].seq

    def _user_rows_through(self, thread_id: str, seq: int) -> int:
        """How many ``user_message`` items sit at or below ordinal *seq*.

        Equivalently: how many ``role: "user"`` rows of the raw history the
        child keeps. Paged rather than fetched with one large limit so a
        thread with a pathological number of user turns still answers.
        """
        total = 0
        cursor: Optional[int] = None
        while True:
            rows = self.store.list_items(
                thread_id,
                limit=_SEGMENT_SCAN,
                kinds=[ItemKind.USER_MESSAGE],
                after_seq=cursor,
                before_seq=seq + 1,
            )
            if not rows:
                return total
            total += len(rows)
            cursor = rows[-1].seq

    def history_cut_for(self, thread_id: str, item: Item) -> tuple[int, bool]:
        """``(user_row_count, include_next_row)`` for the child's history.

        The bridge between the domain truncation and the raw-history
        truncation a fork also has to perform. Counted in USER ROWS, which
        correspond one-to-one with ``user_message`` items; a turn count
        does not, because ``send_user_message`` appends a user row inside a
        running turn.
        """
        self._assert_exact_history_cut(thread_id, item)
        seen = self._user_rows_through(thread_id, item.seq)
        if item.kind is ItemKind.USER_MESSAGE:
            # Keep everything before this question, plus the question.
            return seen - 1, True
        return seen, False

    # -- plans & run diffs (tkt_FqUypyxF2tzC) ----------------------------

    def get_plan(self, thread_id: str, *, scope: str | None = None) -> Dict[str, Any]:
        """The live plan for *scope*, plus every plan the thread has held.

        A dedicated read rather than "filter ``list_items`` to ``plan``"
        because the client question is "what is the plan **now**", and
        answering it from a page means knowing the scope conventions, the
        generation scheme, and which of several plan items is still open.
        Those are server invariants; encoding them in every client is how
        clients drift apart.
        """
        self.ensure_thread(thread_id)
        plans = PlanStore(self.store)
        requested_scope = scope if isinstance(scope, str) and scope else "main"
        current = plans.current(thread_id, requested_scope)
        history = plans.list_plans(thread_id)
        return {
            "thread_id": thread_id,
            "scope": requested_scope,
            "plan": current.to_wire() if current is not None else None,
            "plans": [entry.to_wire() for entry in history],
        }

    def get_run_diff(
        self, thread_id: str, *, turn_id: str | None = None
    ) -> Dict[str, Any]:
        """The aggregated diff for one turn — the latest turn by default.

        ``run_diff`` is ``None`` for a turn that changed no files. That is a
        real, common answer (a question-answering turn), so it is not an
        error; an unknown ``turn_id``, which can only be a client mistake,
        is (:class:`TurnNotFound`).
        """
        self.ensure_thread(thread_id)
        resolved = turn_id
        if resolved is not None:
            if self.store.get_turn(thread_id, resolved) is None:
                raise TurnNotFound(thread_id, resolved)
        else:
            latest = self.store.list_turns(thread_id, limit=1, descending=True)
            resolved = latest[0].turn_id if latest else None
        if resolved is None:
            return {"thread_id": thread_id, "turn_id": None, "run_diff": None}
        items = self.store.list_items(
            thread_id,
            limit=1,
            turn_id=resolved,
            kinds=[ItemKind.RUN_DIFF],
            descending=True,
        )
        if not items:
            return {"thread_id": thread_id, "turn_id": resolved, "run_diff": None}
        item = items[0]
        return {
            "thread_id": thread_id,
            "turn_id": resolved,
            "run_diff": {
                **item.content,
                "item_id": item.item_id,
                "status": item.status.value,
                "revision": item.revision,
                "updated_at": item.updated_at,
            },
        }


def _parse_kinds(kinds: Sequence[str] | None) -> Optional[List[ItemKind]]:
    """Parse a client kind filter. ``None`` means "no filter".

    Unrecognized kinds are dropped rather than rejected: a newer client
    asking for a kind this server does not know should get a narrower page,
    not a hard failure. When *every* requested kind is unknown the caller
    gets an empty result (see :meth:`ConversationService.list_items`), which
    is the honest answer — this server holds no items of those kinds.
    """
    if not kinds:
        return None
    parsed: List[ItemKind] = []
    for raw in kinds:
        try:
            parsed.append(ItemKind(raw))
        except ValueError:
            continue
    return parsed


def _to_page(rows: List[Any], size: int, position: Any) -> Page[Any]:
    has_more = len(rows) > size
    entries = rows[:size]
    next_cursor = encode_cursor(position(entries[-1])) if has_more and entries else None
    return Page(entries=entries, next_cursor=next_cursor, has_more=has_more)
