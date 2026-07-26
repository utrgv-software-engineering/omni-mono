"""Tests for the SDK-compaction interruption guard
(``core/session/compaction._guarded_compaction_session_cls``).

The should_trigger hook declines compaction while a function_call has no
output — but the SDK's deferred-compaction path re-invokes
``run_compaction(force=True)`` from interruption finalization, bypassing
the hook. The guard subclass protects the actual compact call.
"""

from __future__ import annotations

import asyncio

from omniagents.core.session.compaction import _guarded_compaction_session_cls


class FakeCompactionSession:
    """Mimics the OpenAIResponsesCompactionSession surface the guard uses."""

    def __init__(self, session_items):
        self._items = session_items
        self.compacted_with = None

    async def _ensure_compaction_candidates(self):
        return list(self._items), list(self._items)

    async def run_compaction(self, args=None):
        self.compacted_with = args


def _make(items):
    cls = _guarded_compaction_session_cls(FakeCompactionSession)
    return cls(items)


def test_forced_compaction_skipped_over_unresolved_call():
    """The production 400: interruption finalize forces compaction while a
    tool call is still in flight. The guard defers instead of 400ing."""
    session = _make(
        [
            {"type": "function_call", "call_id": "call_1", "name": "ls"},
            # no function_call_output for call_1 — run interrupted mid-call
        ]
    )
    asyncio.run(session.run_compaction({"force": True}))
    assert session.compacted_with is None  # base run_compaction never reached


def test_compaction_proceeds_when_pairs_complete():
    session = _make(
        [
            {"type": "function_call", "call_id": "call_1", "name": "ls"},
            {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
            {"role": "assistant", "content": "done"},
        ]
    )
    args = {"force": True}
    asyncio.run(session.run_compaction(args))
    assert session.compacted_with is args  # passed through to the base


def test_guard_class_is_cached_per_base():
    assert _guarded_compaction_session_cls(
        FakeCompactionSession
    ) is _guarded_compaction_session_cls(FakeCompactionSession)
