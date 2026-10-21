"""Canonical conversation domain objects: Thread, Turn, Item.

The typed, persisted structure over what OmniAgents already stores —
sessions, runs, history messages, tool events, approvals, elicitations,
plans, artifacts, usage, compaction. Nothing here invents a second source
of truth: every field is a *promotion* of state the runtime already
produces (see :mod:`omniagents.core.conversation.recorder` for the
notification → domain mapping and
:mod:`omniagents.core.conversation.adapter` for the legacy projection).

Layout::

    model.py      dataclasses, enums, canonical id derivation, cursors
    store.py      ConversationStore ABC + SQLite and Postgres backends
    recorder.py   write path — fed from stamp_session_event()
    adapter.py    compatibility projection over legacy history rows
    service.py    read path — paginated reads + lazy backfill
    plans.py      durable structured plans over ItemKind.PLAN
    run_diff.py   run-level aggregated unified diffs over ItemKind.RUN_DIFF
"""

from .model import (
    ITEM_SCHEMA_VERSION,
    Item,
    ItemKind,
    ItemStatus,
    Page,
    TERMINAL_ITEM_STATUSES,
    TERMINAL_TURN_STATUSES,
    Thread,
    ThreadStatus,
    ToolKind,
    Turn,
    TurnStatus,
    classify_tool,
    decode_cursor,
    derive_item_id,
    derive_legacy_turn_id,
    encode_cursor,
)
from .plans import Plan, PlanStep, PlanStore
from .recorder import ConversationRecorder, record_session_event
from .run_diff import RunDiffAccumulator, RunDiffObserver
from .service import ConversationService
from .store import ConversationStore, get_conversation_store, reset_conversation_store

__all__ = [
    "ITEM_SCHEMA_VERSION",
    "TERMINAL_ITEM_STATUSES",
    "TERMINAL_TURN_STATUSES",
    "ConversationRecorder",
    "ConversationService",
    "ConversationStore",
    "Item",
    "ItemKind",
    "ItemStatus",
    "Page",
    "Plan",
    "PlanStep",
    "PlanStore",
    "RunDiffAccumulator",
    "RunDiffObserver",
    "Thread",
    "ThreadStatus",
    "ToolKind",
    "Turn",
    "TurnStatus",
    "classify_tool",
    "decode_cursor",
    "derive_item_id",
    "derive_legacy_turn_id",
    "encode_cursor",
    "get_conversation_store",
    "record_session_event",
    "reset_conversation_store",
]
