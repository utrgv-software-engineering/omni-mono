"""Write path: promote runtime events into persisted domain objects.

The recorder is fed from :func:`omniagents.core.session.event_log
.stamp_session_event` — the module's own docstring calls it "the single
chokepoint used by every original emission site". Hooking there means the
recorder sees every notification exactly once, in emission order, from all
three producers (``AgentService._broadcast_session``, the runtime bridge
broadcast, and ``request_client_function``), with no new fan-out to keep in
sync. Replay paths re-send *stored* payloads and never call the chokepoint
again, so a reconnecting client cannot double-record.

Two events reach the recorder outside that chokepoint, because they are
persistence facts rather than notifications:

* ``send_user_message`` — appends to history mid-run without broadcasting.
* the compaction checkpoint — ``run_checkpoint_compaction`` appends a
  summary item to history directly.

Both call :class:`ConversationRecorder` explicitly.

Failure policy: recording is best-effort *for the run*. A storage fault
must never abort a user's turn, so every public entry point swallows and
logs. It does not swallow silently — ``logger.exception`` fires, and the
absence of rows is what the tests assert against, so a broken recorder
fails the suite rather than passing it quietly.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from .model import (
    Item,
    ItemKind,
    ItemStatus,
    Thread,
    ThreadStatus,
    Turn,
    TurnStatus,
    classify_tool,
    content_key,
    derive_item_id,
    derive_title,
    terminal_status_for_end_reason,
)
from .store import ConversationStore, get_conversation_store

logger = logging.getLogger(__name__)

__all__ = [
    "ConversationRecorder",
    "ensure_conversation_thread",
    "record_session_event",
    "recorder_for_session",
]

#: Notifications the recorder consumes. Everything else that flows through
#: the chokepoint is deliberately NOT a conversation item:
#:
#: ``queue_changed``          pending-queue snapshot, not conversation content
#: ``client_request_resolved`` transport ack for a client_request
#: ``session_variables_changed`` session metadata, already persisted
#: ``run_status``             consumed, but as a turn field (retry attempts)
_HANDLED = frozenset(
    {
        "run_started",
        "run_status",
        "message_output",
        "tool_called",
        "tool_result",
        "token",
        "run_end",
        "tool_approval_requested",
        "tool_approval_resolved",
        "mcp_approval_requested",
        "mcp_approval_resolved",
        "elicitation_requested",
        "elicitation_resolved",
        "client_request",
        "session_forked",
    }
)

#: ``client_request`` carries UI side-channel traffic. Only the artifact
#: function is conversation content; status/state pushes are presence.
_ARTIFACT_FUNCTION = "ui.add_artifact"


#: Upper bound on how much of a parent transcript a fork copies in one
#: pass. Forks are user-initiated and bounded in practice; the cap keeps a
#: pathological thread from turning one RPC into an unbounded write.
_FORK_MAX_ITEMS = 100_000


def _now() -> float:
    return time.time()


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


class ConversationRecorder:
    """Maps runtime events onto Thread/Turn/Item writes for one shard."""

    def __init__(
        self,
        store: ConversationStore,
        *,
        project_slug: str | None = None,
        agent_slug: str | None = None,
    ) -> None:
        self._store = store
        self._project_slug = project_slug
        self._agent_slug = agent_slug

    def _legacy_history(self, thread_id: str) -> list:
        """Raw history rows for *thread_id*, or ``[]`` if unavailable."""
        try:
            from omniagents.core.session.history_db import load_history

            rows = load_history(
                thread_id,
                project_slug=self._project_slug,
                agent_slug=self._agent_slug,
            )
        except Exception:
            logger.exception(
                "legacy history lookup failed; thread will be recorded without it",
                extra={"thread": thread_id},
            )
            return []
        return [row for row in rows if isinstance(row, dict)]

    # -- thread ---------------------------------------------------------

    def ensure_thread(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
        archived: bool = False,
        created_at: float | None = None,
        project_legacy_history: bool = True,
    ) -> Thread:
        """Get-or-create the thread row. Idempotent.

        **Creating the row projects any pre-existing history first.** This
        ordering is the whole contract: the recorder claims a thread on the
        first notification of a run, and a session being *resumed* already
        has a transcript in the ``history`` table. If the row were created
        empty, that transcript would be permanently invisible — the reader
        cannot project a thread that already exists without risking a
        clobber of live rows. Projecting here, once, at creation time, makes
        "the thread row exists" imply "its prior history has been
        projected", so resume is lossless and reads never need to write.

        ``project_legacy_history=False`` is for callers that materialize the
        thread themselves (a fork copies the parent's domain rows, which are
        strictly richer than a projection of the same conversation).
        """
        thread = self._store.get_thread(thread_id)
        if thread is None and project_legacy_history:
            history = self._legacy_history(thread_id)
            if history:
                from .adapter import backfill_thread

                projected = backfill_thread(
                    self._store,
                    thread_id,
                    history,
                    user_id=user_id,
                    archived=archived,
                )
                if projected is not None:
                    thread = projected
        if thread is not None:
            changed = False
            if user_id and thread.user_id != user_id:
                thread.user_id = user_id
                changed = True
            status = ThreadStatus.ARCHIVED if archived else ThreadStatus.ACTIVE
            if thread.status is not status:
                thread.status = status
                changed = True
            if changed:
                thread.updated_at = _now()
                self._store.upsert_thread(thread)
            return thread
        now = created_at if created_at is not None else _now()
        thread = Thread(
            thread_id=thread_id,
            user_id=user_id,
            status=ThreadStatus.ARCHIVED if archived else ThreadStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            # Nothing to project (or the caller materializes the thread
            # itself, e.g. a fork copying its parent's domain rows). Either
            # way the question is settled now — recording that explicitly is
            # what stops a later append's history from being re-projected
            # over live rows.
            projected_at=now if project_legacy_history else None,
        )
        self._store.upsert_thread(thread)
        return thread

    def _touch_thread(
        self,
        thread_id: str,
        *,
        item_delta: int = 0,
        turn_delta: int = 0,
        usage: Dict[str, Any] | None = None,
    ) -> None:
        thread = self._store.get_thread(thread_id)
        if thread is None:
            return
        thread.item_count += item_delta
        thread.turn_count += turn_delta
        if usage:
            thread.usage = _merge_usage(thread.usage, usage)
        thread.updated_at = _now()
        self._store.upsert_thread(thread)

    # -- items ----------------------------------------------------------

    #: Upper bound on the occurrence-ordinal probe. Far beyond any real
    #: run; exists so a pathological thread cannot spin here.
    _MAX_OCCURRENCES = 10_000

    def _next_occurrence_key(self, thread_id: str, base_key: str) -> str:
        """``base_key`` plus the first unused occurrence ordinal.

        Content-digest keys make re-delivery idempotent, but they also
        collapse genuinely repeated content: a user who sends "continue"
        twice in one run would lose a message from the canonical
        transcript, and in this codebase the transcript is a record — we do
        not drop content on a redundancy judgment.

        The ordinal restores distinctness while staying **client-derivable**:
        a client counts its own repetitions of the same content in the same
        run and arrives at the same id. It is a probe rather than a stored
        counter so it needs no extra state and stays deterministic under
        replay of the same sequence.
        """
        for occurrence in range(self._MAX_OCCURRENCES):
            key = f"{base_key}:{occurrence}"
            if self._store.get_item(thread_id, derive_item_id(thread_id, key)) is None:
                return key
        return f"{base_key}:{self._MAX_OCCURRENCES}"

    def _append_item(
        self,
        thread_id: str,
        *,
        natural_key: str,
        kind: ItemKind,
        turn_id: str | None,
        status: ItemStatus,
        role: str | None = None,
        content: Dict[str, Any] | None = None,
        source_ref: Dict[str, Any] | None = None,
    ) -> Optional[Item]:
        """Create the item if it is new; otherwise return the existing one.

        Creation is keyed on the derived id, so a re-delivered ``started``
        event (the same ``call_id`` arriving twice) never allocates a second
        ordinal — which is what keeps ``seq`` dense and the transcript
        free of phantom duplicates.
        """
        item_id = derive_item_id(thread_id, natural_key)
        existing = self._store.get_item(thread_id, item_id)
        if existing is not None:
            return existing
        try:
            seq = self._store.allocate_seq(thread_id)
        except KeyError:
            self.ensure_thread(thread_id)
            seq = self._store.allocate_seq(thread_id)
        now = _now()
        item = Item(
            item_id=item_id,
            thread_id=thread_id,
            turn_id=turn_id,
            seq=seq,
            kind=kind,
            status=status,
            role=role,
            created_at=now,
            updated_at=now,
            completed_at=now if status is not ItemStatus.STARTED else None,
            revision=0,
            content=_as_dict(content),
            source_ref=_as_dict(source_ref),
        )
        self._store.upsert_item(item)
        self._touch_thread(thread_id, item_delta=1)
        self._extend_turn_window(thread_id, turn_id, seq)
        return item

    def _revise_item(
        self,
        thread_id: str,
        natural_key: str,
        *,
        content: Dict[str, Any],
    ) -> Optional[Item]:
        """Replace the CONTENT of an already-terminal item.

        The terminal-once rule protects an item's *status* — the answer to
        "how did this end" must never change. It is not a rule about
        content, and one kind genuinely revises: re-issuing
        ``ui.add_artifact`` with the same ``artifact_id`` means "here is the
        new version of that artifact". Bumping ``revision`` records the
        change so a client can tell a stale cached copy from a current one,
        while ``status``/``completed_at`` stay untouched.
        """
        item_id = derive_item_id(thread_id, natural_key)
        existing = self._store.get_item(thread_id, item_id)
        if existing is None:
            return None
        merged = {**existing.content, **content}
        if merged == existing.content:
            return existing
        now = _now()
        revised = Item(
            item_id=existing.item_id,
            thread_id=existing.thread_id,
            turn_id=existing.turn_id,
            seq=existing.seq,
            kind=existing.kind,
            status=existing.status,
            role=existing.role,
            created_at=existing.created_at,
            updated_at=now,
            completed_at=existing.completed_at,
            revision=existing.revision + 1,
            content=merged,
            source_ref=dict(existing.source_ref),
            long_lived=existing.long_lived,
            source=existing.source,
            schema_version=existing.schema_version,
        )
        self._store.upsert_item(revised)
        return revised

    def _update_item(
        self,
        thread_id: str,
        natural_key: str,
        *,
        status: ItemStatus | None = None,
        content: Dict[str, Any] | None = None,
    ) -> Optional[Item]:
        """Advance an existing item. No-op when the item is unknown."""
        item_id = derive_item_id(thread_id, natural_key)
        existing = self._store.get_item(thread_id, item_id)
        if existing is None:
            return None
        updated = existing.with_transition(status=status, content=content, now=_now())
        if updated is existing:
            # Terminal already: the first terminal state wins, by contract.
            return existing
        self._store.upsert_item(updated)
        return updated

    def _extend_turn_window(
        self, thread_id: str, turn_id: str | None, seq: int
    ) -> None:
        if not turn_id:
            return
        turn = self._store.get_turn(thread_id, turn_id)
        if turn is None:
            return
        turn.item_count += 1
        turn.first_seq = seq if turn.first_seq is None else min(turn.first_seq, seq)
        turn.last_seq = seq if turn.last_seq is None else max(turn.last_seq, seq)
        turn.updated_at = _now()
        self._store.upsert_turn(turn)

    # -- turns ----------------------------------------------------------

    def open_turn(
        self,
        thread_id: str,
        turn_id: str,
        *,
        prompt: str | None,
        prompt_role: str | None,
        user_id: str | None = None,
    ) -> Turn:
        self.ensure_thread(thread_id, user_id=user_id)
        existing = self._store.get_turn(thread_id, turn_id)
        if existing is not None:
            return existing
        now = _now()
        ordinal = self._store.max_turn_ordinal(thread_id) + 1
        turn = Turn(
            turn_id=turn_id,
            thread_id=thread_id,
            ordinal=ordinal,
            status=TurnStatus.RUNNING,
            created_at=now,
            updated_at=now,
            prompt=prompt,
            prompt_role=prompt_role,
        )
        self._store.upsert_turn(turn)
        self._touch_thread(thread_id, turn_delta=1)
        if prompt:
            role = prompt_role or "user"
            if ordinal == 1 and role == "user":
                thread = self._store.get_thread(thread_id)
                if thread is not None and not thread.title:
                    thread.title = derive_title(prompt)
                    thread.updated_at = now
                    self._store.upsert_thread(thread)
            self._append_item(
                thread_id,
                natural_key=f"prompt:{turn_id}",
                kind=(
                    ItemKind.USER_MESSAGE if role == "user" else ItemKind.AGENT_MESSAGE
                ),
                turn_id=turn_id,
                status=ItemStatus.COMPLETED,
                role=role,
                content={"text": prompt, "origin": "prompt"},
                source_ref={"event": "run_started", "run_id": turn_id},
            )
        return turn

    def close_turn(
        self,
        thread_id: str,
        turn_id: str,
        *,
        end_reason: str | None,
        error: Dict[str, Any] | None,
        usage: Dict[str, Any] | None,
        model: str | None,
        model_ref: str | None,
    ) -> None:
        """Land the turn — and every item still open under it — terminally.

        This is the step that makes store-only reconstruction sound: after
        ``run_end`` there is no in-flight state left anywhere. A tool call
        whose result never arrived is not left dangling at ``started``; it
        is resolved to ``cancelled`` (the run was stopped) or ``failed``
        (the run died) with ``finalized_by="run_end"`` recorded, so a reader
        can tell an inferred terminal state from an observed one.
        """
        turn = self._store.get_turn(thread_id, turn_id)
        status = terminal_status_for_end_reason(end_reason)
        now = _now()
        orphan_status = (
            ItemStatus.CANCELLED
            if status is TurnStatus.CANCELLED
            else ItemStatus.FAILED
        )
        # Sweep the THREAD, not just the turn. Some items are recorded with
        # no ``turn_id`` at all — an elicitation or a ``client_request``
        # raised outside a run carries no ``run_id`` — and scoping the sweep
        # to the turn left those permanently ``started``, which is exactly
        # the "in-flight state after run_end" this model exists to rule out.
        # Only one run is active per session, so an untied open item belongs
        # to the run that is ending.
        for item in self._store.open_items(thread_id):
            if item.turn_id is not None and item.turn_id != turn_id:
                continue
            if item.long_lived:
                # Deliberately outlives its turn; the owning subsystem
                # lands it terminally (see Item.long_lived).
                continue
            self._store.upsert_item(
                item.with_transition(
                    status=orphan_status,
                    content={"finalized_by": "run_end", "end_reason": end_reason},
                    now=now,
                )
            )
        if turn is None:
            return
        if turn.status in (
            TurnStatus.COMPLETED,
            TurnStatus.FAILED,
            TurnStatus.CANCELLED,
        ):
            return
        turn.status = status
        turn.end_reason = end_reason
        turn.error = error
        turn.completed_at = now
        turn.updated_at = now
        if usage:
            # ``run_end.usage`` is the run's FINAL total, not a delta, and
            # so are the ``token`` totals it supersedes — merging them would
            # double-count every turn. Replace, then roll the settled turn
            # total up into the thread.
            turn.usage = dict(usage)
        if model:
            turn.model = model
        if model_ref:
            turn.model_ref = model_ref
        self._store.upsert_turn(turn)
        self._touch_thread(thread_id, usage=turn.usage or None)

    # -- event dispatch --------------------------------------------------

    def record(self, thread_id: str, method: str, payload: Dict[str, Any]) -> None:
        if method not in _HANDLED:
            return
        handler = getattr(self, f"_on_{method}")
        handler(thread_id, payload)

    def _on_run_started(self, thread_id: str, payload: Dict[str, Any]) -> None:
        run_id = payload.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            return
        prompt = payload.get("prompt")
        self.open_turn(
            thread_id,
            run_id,
            prompt=prompt if isinstance(prompt, str) else None,
            prompt_role=(
                payload.get("prompt_role")
                if isinstance(payload.get("prompt_role"), str)
                else None
            ),
        )

    def _on_run_status(self, thread_id: str, payload: Dict[str, Any]) -> None:
        run_id = payload.get("run_id")
        if not isinstance(run_id, str):
            return
        turn = self._store.get_turn(thread_id, run_id)
        if turn is None:
            return
        attempt = payload.get("attempt")
        if isinstance(attempt, int):
            turn.attempts = max(turn.attempts, attempt)
        turn.updated_at = _now()
        self._store.upsert_turn(turn)

    @staticmethod
    def message_natural_key(
        turn_id: str | None,
        content: str,
        message_id: str | None = None,
        occurrence: int = 0,
    ) -> str:
        """Natural key for an assistant message.

        The model's own ``msg_*`` id when the provider minted one — already
        unique, so no ordinal is needed. Otherwise a digest of the text
        scoped to the turn, plus an occurrence ordinal so two identical
        messages stay two items. Both forms are computable by a client from
        the ``message_output`` payloads it received; neither is a
        server-side position the client could not reproduce.
        """
        if isinstance(message_id, str) and message_id:
            return f"message:{message_id}"
        return f"message:{turn_id}:{content_key(content)}:{occurrence}"

    def _on_message_output(self, thread_id: str, payload: Dict[str, Any]) -> None:
        run_id = payload.get("run_id")
        content = payload.get("content")
        if not isinstance(content, str):
            return
        turn_id = run_id if isinstance(run_id, str) else None
        message_id = payload.get("message_id")
        natural_key = (
            self.message_natural_key(turn_id, content, message_id)
            if isinstance(message_id, str) and message_id
            else self._next_occurrence_key(
                thread_id, f"message:{turn_id}:{content_key(content)}"
            )
        )
        self._append_item(
            thread_id,
            natural_key=natural_key,
            kind=ItemKind.AGENT_MESSAGE,
            turn_id=turn_id,
            status=ItemStatus.COMPLETED,
            role="assistant",
            content={
                "text": content,
                **({"message_id": message_id} if isinstance(message_id, str) else {}),
            },
            source_ref={
                "event": "message_output",
                "run_id": run_id,
                **({"message_id": message_id} if isinstance(message_id, str) else {}),
            },
        )

    @staticmethod
    def _tool_natural_key(payload: Dict[str, Any]) -> Optional[str]:
        call_id = payload.get("call_id")
        if isinstance(call_id, str) and call_id:
            return f"tool:{call_id}"
        # A tool event with no call_id cannot be correlated to its result;
        # recording a half of the pair would produce an item that can never
        # reach a terminal state through the normal path.
        return None

    def _on_tool_called(self, thread_id: str, payload: Dict[str, Any]) -> None:
        key = self._tool_natural_key(payload)
        if key is None:
            return
        tool = payload.get("tool")
        server_label = payload.get("server_label")
        self._append_item(
            thread_id,
            natural_key=key,
            kind=ItemKind.TOOL_CALL,
            turn_id=(
                payload.get("run_id")
                if isinstance(payload.get("run_id"), str)
                else None
            ),
            status=ItemStatus.STARTED,
            role="assistant",
            content={
                "tool": tool,
                "tool_kind": classify_tool(
                    tool if isinstance(tool, str) else None,
                    server_label=(
                        server_label if isinstance(server_label, str) else None
                    ),
                ).value,
                "input": payload.get("input"),
                **(
                    {"server_label": server_label}
                    if isinstance(server_label, str)
                    else {}
                ),
                **(
                    {"tool_label": payload["tool_label"]}
                    if isinstance(payload.get("tool_label"), str)
                    else {}
                ),
            },
            source_ref={"event": "tool_called", "call_id": payload.get("call_id")},
        )

    def _on_tool_result(self, thread_id: str, payload: Dict[str, Any]) -> None:
        key = self._tool_natural_key(payload)
        if key is None:
            return
        metadata = _as_dict(payload.get("metadata"))
        failed = metadata.get("display_type") == "error"
        content: Dict[str, Any] = {"output": payload.get("output")}
        if metadata:
            content["metadata"] = metadata
        self._update_item(
            thread_id,
            key,
            status=ItemStatus.FAILED if failed else ItemStatus.COMPLETED,
            content=content,
        )

    def _on_token(self, thread_id: str, payload: Dict[str, Any]) -> None:
        run_id = payload.get("run_id")
        if not isinstance(run_id, str):
            return
        turn = self._store.get_turn(thread_id, run_id)
        if turn is None:
            return
        totals = _as_dict(payload.get("totals"))
        if totals:
            # ``totals`` are cumulative for the run, so they REPLACE rather
            # than accumulate — adding them would multiply the run's usage
            # by the number of streamed deltas.
            turn.usage = dict(totals)
        for field_name in ("model", "model_ref"):
            value = payload.get(field_name)
            if isinstance(value, str) and value:
                setattr(turn, field_name, value)
        turn.updated_at = _now()
        self._store.upsert_turn(turn)

    def _on_run_end(self, thread_id: str, payload: Dict[str, Any]) -> None:
        run_id = payload.get("run_id")
        if not isinstance(run_id, str):
            return
        self.close_turn(
            thread_id,
            run_id,
            end_reason=(
                payload.get("end_reason")
                if isinstance(payload.get("end_reason"), str)
                else None
            ),
            error=_as_dict(payload.get("error")) or None,
            usage=_as_dict(payload.get("usage")) or None,
            model=(
                payload.get("model") if isinstance(payload.get("model"), str) else None
            ),
            model_ref=(
                payload.get("model_ref")
                if isinstance(payload.get("model_ref"), str)
                else None
            ),
        )

    def _on_tool_approval_requested(
        self, thread_id: str, payload: Dict[str, Any]
    ) -> None:
        call_id = payload.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            return
        tool_name = payload.get("tool_name")
        self._append_item(
            thread_id,
            natural_key=f"approval:tool:{call_id}",
            kind=ItemKind.APPROVAL,
            turn_id=(
                payload.get("run_id")
                if isinstance(payload.get("run_id"), str)
                else None
            ),
            status=ItemStatus.STARTED,
            content={
                "approval_kind": "tool",
                "tool": tool_name,
                "tool_kind": classify_tool(
                    tool_name if isinstance(tool_name, str) else None
                ).value,
                "arguments": payload.get("arguments"),
                "call_id": call_id,
            },
            source_ref={"event": "tool_approval_requested", "call_id": call_id},
        )

    def _on_tool_approval_resolved(
        self, thread_id: str, payload: Dict[str, Any]
    ) -> None:
        call_id = payload.get("call_id")
        if not isinstance(call_id, str):
            return
        self._update_item(
            thread_id,
            f"approval:tool:{call_id}",
            status=ItemStatus.COMPLETED,
            content={
                key: payload[key] for key in ("reason",) if payload.get(key) is not None
            },
        )

    def _on_mcp_approval_requested(
        self, thread_id: str, payload: Dict[str, Any]
    ) -> None:
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            return
        self._append_item(
            thread_id,
            natural_key=f"approval:mcp:{request_id}",
            kind=ItemKind.APPROVAL,
            turn_id=(
                payload.get("run_id")
                if isinstance(payload.get("run_id"), str)
                else None
            ),
            status=ItemStatus.STARTED,
            content={
                "approval_kind": "mcp",
                "tool_kind": "mcp",
                "server_label": payload.get("server_label"),
                "tool": payload.get("tool_name"),
                "arguments": payload.get("arguments"),
                "request_id": request_id,
            },
            source_ref={"event": "mcp_approval_requested", "request_id": request_id},
        )

    def _on_mcp_approval_resolved(
        self, thread_id: str, payload: Dict[str, Any]
    ) -> None:
        request_id = payload.get("request_id")
        if not isinstance(request_id, str):
            return
        self._update_item(
            thread_id,
            f"approval:mcp:{request_id}",
            status=ItemStatus.COMPLETED,
            content={
                key: payload[key] for key in ("reason",) if payload.get(key) is not None
            },
        )

    def _on_elicitation_requested(
        self, thread_id: str, payload: Dict[str, Any]
    ) -> None:
        elicitation_id = payload.get("elicitation_id")
        if not isinstance(elicitation_id, str) or not elicitation_id:
            return
        self._append_item(
            thread_id,
            natural_key=f"elicitation:{elicitation_id}",
            kind=ItemKind.ELICITATION,
            turn_id=(
                payload.get("run_id")
                if isinstance(payload.get("run_id"), str)
                else None
            ),
            status=ItemStatus.STARTED,
            content={
                "elicitation_id": elicitation_id,
                "elicitation_kind": payload.get("kind"),
                "message": payload.get("message"),
                **{
                    key: payload[key]
                    for key in ("title", "options", "input_schema", "url", "source")
                    if payload.get(key) is not None
                },
            },
            source_ref={
                "event": "elicitation_requested",
                "elicitation_id": elicitation_id,
            },
        )

    def _on_elicitation_resolved(self, thread_id: str, payload: Dict[str, Any]) -> None:
        elicitation_id = payload.get("elicitation_id")
        if not isinstance(elicitation_id, str):
            return
        status = payload.get("status")
        self._update_item(
            thread_id,
            f"elicitation:{elicitation_id}",
            status=(
                ItemStatus.CANCELLED
                if status in ("cancelled", "expired")
                else ItemStatus.COMPLETED
            ),
            content={
                key: payload[key]
                for key in ("status", "action", "value", "reason")
                if payload.get(key) is not None
            },
        )

    def _on_client_request(self, thread_id: str, payload: Dict[str, Any]) -> None:
        if payload.get("function") != _ARTIFACT_FUNCTION:
            return
        args = _as_dict(payload.get("args"))
        artifact_id = args.get("artifact_id")
        request_id = payload.get("request_id")
        # Prefer the agent-supplied artifact id: re-displaying the same
        # artifact then updates one item instead of appending a duplicate.
        key = (
            f"artifact:{artifact_id}"
            if isinstance(artifact_id, str) and artifact_id
            else f"artifact:req:{request_id}"
        )
        artifact_content = {
            "artifact_id": artifact_id,
            "title": args.get("title"),
            "mode": args.get("mode"),
            "content": args.get("content"),
        }
        if self._store.get_item(thread_id, derive_item_id(thread_id, key)) is not None:
            # Re-display of an artifact the agent already published: this is
            # a new VERSION, not a duplicate. Appending would show the stale
            # copy forever; ignoring it would drop the update outright.
            self._revise_item(thread_id, key, content=artifact_content)
            return
        self._append_item(
            thread_id,
            natural_key=key,
            kind=ItemKind.ARTIFACT,
            turn_id=(
                payload.get("run_id")
                if isinstance(payload.get("run_id"), str)
                else None
            ),
            status=ItemStatus.COMPLETED,
            role="assistant",
            content=artifact_content,
            source_ref={"event": "client_request", "request_id": request_id},
        )

    def _on_session_forked(self, thread_id: str, payload: Dict[str, Any]) -> None:
        new_thread_id = payload.get("new_session_id")
        if not isinstance(new_thread_id, str) or not new_thread_id:
            return
        self.record_fork(thread_id, new_thread_id)

    # -- explicit (non-notification) entry points -------------------------

    def record_fork(self, parent_thread_id: str, new_thread_id: str) -> None:
        """Materialize a forked thread, then stamp the branch point.

        ``SessionManager.fork_session`` copies the parent's *history rows*
        into the new session, so a fork genuinely has a transcript. Writing
        only the ancestry fields left the child readable but EMPTY — which
        is what a client rendered.

        The child is materialized by copying the parent's DOMAIN rows rather
        than re-projecting the copied history, because a projection is
        lossy: approvals, artifacts and elicitations never appear in the raw
        history table at all, and tool taxonomy would be re-derived instead
        of preserved. Item ids are re-derived for the child thread (ids are
        thread-scoped by construction) with the originating id kept in
        ``source_ref``, so the two transcripts are independently addressable
        yet traceable to one another.

        When the parent has no domain rows — a purely legacy session being
        forked — the child is left for the normal creation-time projection
        to materialize from its own copied history; the ancestry stamped
        here survives that (see ``backfill_thread``).
        """
        parent = self.ensure_thread(parent_thread_id)
        existing_child = self._store.get_thread(new_thread_id)
        if existing_child is not None and self._store.count_items(new_thread_id) > 0:
            # Already materialized (duplicate ``session_forked`` delivery).
            return

        source_items = self._store.list_items(parent_thread_id, limit=_FORK_MAX_ITEMS)
        branch_item_id = source_items[-1].item_id if source_items else None

        child = self.ensure_thread(
            new_thread_id,
            user_id=parent.user_id,
            # The copy below IS the child's materialization; projecting its
            # duplicated history first would write the same conversation
            # twice, in the poorer of the two forms.
            project_legacy_history=not source_items,
        )
        child.parent_thread_id = parent_thread_id
        child.branched_from_item_id = branch_item_id
        child.updated_at = _now()
        if not source_items:
            self._store.upsert_thread(child)
            return

        source_turns = self._store.list_turns(parent_thread_id, limit=_FORK_MAX_ITEMS)
        child.title = parent.title
        child.last_seq = parent.last_seq
        child.turn_count = len(source_turns)
        child.item_count = len(source_items)
        child.usage = dict(parent.usage)
        child.compaction = dict(parent.compaction)
        child.source = parent.source
        # The copy IS the child's materialization.
        child.projected_at = _now()
        self._store.write_projection(
            child,
            [self._fork_turn(new_thread_id, turn) for turn in source_turns],
            [self._fork_item(new_thread_id, item) for item in source_items],
        )

    @staticmethod
    def _fork_turn(new_thread_id: str, turn: Turn) -> Turn:
        """One parent turn, re-addressed under the forked thread."""
        return Turn(
            turn_id=turn.turn_id,
            thread_id=new_thread_id,
            ordinal=turn.ordinal,
            status=turn.status,
            created_at=turn.created_at,
            updated_at=turn.updated_at,
            completed_at=turn.completed_at,
            prompt=turn.prompt,
            prompt_role=turn.prompt_role,
            end_reason=turn.end_reason,
            error=dict(turn.error) if turn.error else None,
            usage=dict(turn.usage),
            model=turn.model,
            model_ref=turn.model_ref,
            item_count=turn.item_count,
            first_seq=turn.first_seq,
            last_seq=turn.last_seq,
            attempts=turn.attempts,
            source=turn.source,
            schema_version=turn.schema_version,
        )

    @staticmethod
    def _fork_item(new_thread_id: str, item: Item) -> Item:
        """One parent item, re-addressed under the forked thread.

        An item still in flight when the fork is taken is landed terminally
        in the CHILD. The child is a new conversation: the parent's run will
        finish that tool call and finalize the parent's copy, but no
        ``run_end`` for that run ever reaches this thread, so an open copy
        here would stay ``started`` forever and falsify the "no in-flight
        state after run_end" guarantee for a reachable case. ``cancelled``
        is the honest terminal state — from the child's point of view the
        work was never carried out — and ``finalized_by="fork"`` marks it as
        inferred rather than observed. ``long_lived`` items are exempt, as
        everywhere else.
        """
        status = item.status
        content = dict(item.content)
        completed_at = item.completed_at
        revision = item.revision
        if not item.is_terminal and not item.long_lived:
            status = ItemStatus.CANCELLED
            content["finalized_by"] = "fork"
            completed_at = _now()
            revision += 1
        return Item(
            item_id=derive_item_id(new_thread_id, f"forked:{item.item_id}"),
            thread_id=new_thread_id,
            turn_id=item.turn_id,
            seq=item.seq,
            kind=item.kind,
            status=status,
            role=item.role,
            created_at=item.created_at,
            updated_at=item.updated_at,
            completed_at=completed_at,
            revision=revision,
            content=content,
            source_ref={
                **item.source_ref,
                "forked_from_thread_id": item.thread_id,
                "forked_from_item_id": item.item_id,
            },
            long_lived=item.long_lived,
            source=item.source,
            schema_version=item.schema_version,
        )

    def record_reasoning(
        self, thread_id: str, *, run_id: str | None, item: Dict[str, Any]
    ) -> Optional[Item]:
        """A model reasoning block, from the history-append path.

        Reasoning has no notification — it is persisted straight to history
        by the SDK — so without this hook the recorded model would hold no
        reasoning at all while the legacy projection does, and the two
        producers of one surface would disagree about the same conversation.
        Recorded from ``Session.append_message`` for the same reason
        compaction checkpoints are: the fact never reaches the broadcast
        chokepoint.
        """
        summary = item.get("summary")
        parts: list[str] = []
        if isinstance(summary, list):
            for entry in summary:
                if isinstance(entry, str):
                    parts.append(entry)
                elif isinstance(entry, dict) and isinstance(entry.get("text"), str):
                    parts.append(entry["text"])
        text = "\n".join(parts)
        reasoning_id = item.get("id")
        key = (
            f"reasoning:{reasoning_id}"
            if isinstance(reasoning_id, str) and reasoning_id
            else self._next_occurrence_key(
                thread_id, f"reasoning:{run_id}:{content_key(text)}"
            )
        )
        return self._append_item(
            thread_id,
            natural_key=key,
            kind=ItemKind.REASONING,
            turn_id=run_id,
            status=ItemStatus.COMPLETED,
            role="assistant",
            content={
                "summary": text,
                **(
                    {"reasoning_id": reasoning_id}
                    if isinstance(reasoning_id, str)
                    else {}
                ),
            },
            source_ref={"event": "history_append", "run_id": run_id},
        )

    @staticmethod
    def user_message_natural_key(
        run_id: str | None, content: str, occurrence: int = 0
    ) -> str:
        """Natural key for a mid-run user message.

        ``send_user_message`` carries no id, so the key digests the text
        against the run and appends an occurrence ordinal. The client sent
        that text and knows how many times it sent it, so it can derive the
        same id — and two identical sends stay two items.
        """
        return f"user:{run_id}:{content_key(content)}:{occurrence}"

    def record_user_message(
        self, thread_id: str, *, run_id: str | None, content: str
    ) -> Optional[Item]:
        """A mid-run user message (``send_user_message``)."""
        return self._append_item(
            thread_id,
            natural_key=self._next_occurrence_key(
                thread_id, f"user:{run_id}:{content_key(content)}"
            ),
            kind=ItemKind.USER_MESSAGE,
            turn_id=run_id,
            status=ItemStatus.COMPLETED,
            role="user",
            content={"text": content},
            source_ref={"event": "send_user_message", "run_id": run_id},
        )

    def record_approval_decision(
        self,
        thread_id: str,
        *,
        approval_kind: str,
        resource_id: str,
        decision: str,
        rejection_message: str | None = None,
        always_approve: bool | None = None,
    ) -> None:
        """Attach the user's answer BEFORE the item goes terminal.

        ``tool_approval_resolved`` / ``mcp_approval_resolved`` carry only the
        correlation id on the wire, so the decision itself is recorded here,
        from the RPC handler that knows it. Ordering matters: the content
        merge has to land while the item is still non-terminal.
        """
        content: Dict[str, Any] = {"decision": decision}
        if rejection_message is not None:
            content["rejection_message"] = rejection_message
        if always_approve is not None:
            content["always_approve"] = bool(always_approve)
        self._update_item(
            thread_id, f"approval:{approval_kind}:{resource_id}", content=content
        )

    def record_compaction_checkpoint(
        self,
        thread_id: str,
        *,
        summary: str,
        kind: str,
        version: int,
        run_id: str | None = None,
    ) -> Optional[Item]:
        """Record a context-compaction checkpoint and move the thread's
        compaction floor to it.

        ``run_id`` ties the checkpoint to the run it happened during, so a
        turn-by-turn reconstruction (``list_items(turn_id=…)``) still sees
        it. Compaction triggered outside any run has no turn, and such an
        item is reachable only through the unscoped read — documented in
        protocol.md rather than left as a silent gap.
        """
        item = self._append_item(
            thread_id,
            natural_key=self._next_occurrence_key(
                thread_id, f"compaction:{content_key(summary)}"
            ),
            kind=ItemKind.COMPACTION,
            turn_id=run_id,
            status=ItemStatus.COMPLETED,
            role="assistant",
            content={"summary": summary, "checkpoint_kind": kind, "version": version},
            source_ref={"event": "compaction_checkpoint"},
        )
        if item is None:
            return None
        thread = self._store.get_thread(thread_id)
        if thread is not None:
            thread.compaction = {
                "checkpoint_item_id": item.item_id,
                "checkpoint_seq": item.seq,
                "compacted_below_seq": item.seq,
            }
            thread.updated_at = _now()
            self._store.upsert_thread(thread)
        return item

    def record_archived(self, thread_id: str, archived: bool) -> None:
        thread = self._store.get_thread(thread_id)
        if thread is None:
            return
        thread.status = ThreadStatus.ARCHIVED if archived else ThreadStatus.ACTIVE
        thread.updated_at = _now()
        self._store.upsert_thread(thread)


def _merge_usage(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """Sum numeric usage counters, last-write-wins for everything else."""
    merged = dict(existing)
    for key, value in incoming.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            current = merged.get(key)
            merged[key] = (
                current + value
                if isinstance(current, (int, float)) and not isinstance(current, bool)
                else value
            )
        else:
            merged[key] = value
    return merged


def recorder_for_session(session: Any) -> Optional[ConversationRecorder]:
    """Build a recorder bound to *session*'s (project, agent) shard."""
    project_slug = getattr(session, "_project_slug", None)
    agent_slug = getattr(session, "_agent_slug", None)
    try:
        store = get_conversation_store(project_slug, agent_slug)
    except Exception:
        logger.exception("conversation store unavailable; domain objects not recorded")
        return None
    return ConversationRecorder(store, project_slug=project_slug, agent_slug=agent_slug)


def ensure_conversation_thread(session: Any) -> Optional[ConversationRecorder]:
    """Materialize *session*'s thread BEFORE a new history row is written.

    Creating a thread projects whatever history already exists. If a caller
    appends its row first and materializes second, the projection sees that
    row and records it — and then the caller records it again, producing a
    duplicate. Callers that persist to history AND record a domain item for
    the same fact (reasoning blocks, compaction checkpoints) therefore call
    this first. Returns the recorder so the caller can reuse it.
    """
    session_id = getattr(session, "id", None)
    if not isinstance(session_id, str) or not session_id:
        return None
    recorder = recorder_for_session(session)
    if recorder is None:
        return None
    recorder.ensure_thread(
        session_id,
        user_id=getattr(session, "user_id", None),
        archived=bool(getattr(session, "archived", False)),
    )
    return recorder


def record_session_event(session: Any, method: str, payload: Dict[str, Any]) -> None:
    """Chokepoint entry point. Never raises."""
    if method not in _HANDLED:
        return
    session_id = getattr(session, "id", None)
    if not isinstance(session_id, str) or not session_id:
        return
    try:
        recorder = recorder_for_session(session)
        if recorder is None:
            return
        recorder.ensure_thread(
            session_id,
            user_id=getattr(session, "user_id", None),
            archived=bool(getattr(session, "archived", False)),
        )
        recorder.record(session_id, method, payload)
    except Exception:
        logger.exception("conversation recording failed", extra={"method": method})
