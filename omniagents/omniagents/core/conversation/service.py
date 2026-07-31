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

import logging
from typing import Any, Dict, List, Optional, Sequence

from .adapter import backfill_thread
from .model import (
    CursorError,
    ItemKind,
    Page,
    Thread,
    decode_cursor,
    encode_cursor,
)
from .store import ConversationStore, get_conversation_store

logger = logging.getLogger(__name__)

__all__ = [
    "ConversationRpcError",
    "ConversationService",
    "InvalidCursor",
    "ItemNotFound",
    "MAX_PAGE_SIZE",
    "ThreadNotFound",
]

#: JSON-RPC error codes. ``-32010..13``, ``-32020``, ``-32030``,
#: ``-32040..42``, ``-32050..52``, ``-32060..63`` and ``-32070..76`` are
#: taken by earlier surfaces; this one opens the ``-3208x`` block.
RPC_ERROR_THREAD_NOT_FOUND = -32080
RPC_ERROR_ITEM_NOT_FOUND = -32081
RPC_ERROR_CURSOR_INVALID = -32082

#: Hard ceiling on a page. A client asking for more gets ``MAX_PAGE_SIZE``
#: plus a cursor, never a 200k-item result that stalls the socket.
MAX_PAGE_SIZE = 500
DEFAULT_PAGE_SIZE = 50


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


class InvalidCursor(ConversationRpcError):
    code = RPC_ERROR_CURSOR_INVALID
    kind = "cursor_invalid"

    def __init__(self, cursor: str) -> None:
        super().__init__("Malformed pagination cursor", cursor=cursor)


def _clamp(limit: Optional[int]) -> int:
    """Clamp a requested page size into ``[1, MAX_PAGE_SIZE]``.

    Clamping rather than rejecting: ``limit=0`` from a client computing a
    window size should return one row, not a typed error mid-scroll. The
    behaviour is documented in protocol.md so it is a contract, not a
    surprise."""
    if limit is None:
        return DEFAULT_PAGE_SIZE
    return max(1, min(int(limit), MAX_PAGE_SIZE))


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
