"""Write path: promote runtime events into persisted domain objects.

The recorder is fed from :func:`omniagents.core.session.event_log
.stamp_session_event` — the module's own docstring calls it "the single
chokepoint used by every original emission site". Hooking there means the
recorder sees every notification exactly once, in emission order, from all
three producers (``AgentService._broadcast_session``, the runtime bridge
broadcast, and ``request_client_function``), with no new fan-out to keep in
sync. Replay paths re-send *stored* payloads and never call the chokepoint
again, so a reconnecting client cannot double-record.

Several facts reach the recorder outside that chokepoint, because they are
persistence facts rather than notifications:

* ``send_user_message`` — appends to history mid-run without broadcasting.
* the compaction checkpoint — ``run_checkpoint_compaction`` appends a
  summary item to history directly.
* persisted reasoning and completed provider messages — SDK persistence
  can precede (or outlive) delivery of the corresponding stream event.

These call :class:`ConversationRecorder` explicitly.

Event projection callers log storage failures on a best-effort basis. Explicit
durability boundaries, including ``commit_approval_decision``, instead propagate
failure. A human approval must commit before its waiter can authorize execution.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from typing import Any, Callable, Dict, Optional

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
    provider_item_id,
    terminal_status_for_end_reason,
)
from .store import ConversationStore, get_conversation_store

logger = logging.getLogger(__name__)

__all__ = [
    "ConversationRecorder",
    "ensure_conversation_thread",
    "push_item_updated",
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
        "tool_approval_reviewed",
        "mcp_approval_requested",
        "mcp_approval_resolved",
        "plan_completion_reviewed",
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


#: How a turn's terminal status maps onto its run-diff item. Not the
#: orphan-sweep status: a COMPLETED turn's orphans are ``failed`` (they never
#: finished), but its diff did finish.
_RUN_DIFF_TERMINAL = {
    TurnStatus.COMPLETED: ItemStatus.COMPLETED,
    TurnStatus.FAILED: ItemStatus.FAILED,
    TurnStatus.CANCELLED: ItemStatus.CANCELLED,
}


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
        on_settled: "Callable[[Item], None] | None" = None,
    ) -> None:
        self._store = store
        self._project_slug = project_slug
        self._agent_slug = agent_slug
        #: Called with every item this recorder lands TERMINALLY on its own
        #: initiative — the run diff at ``run_end`` and the plan at archive.
        #: Those transitions have no notification of their own, so without a
        #: hook a live client's plan and diff would sit at ``started``
        #: forever and only converge on a refetch. Never raises: see
        #: :func:`recorder_for_session`.
        self._on_settled = on_settled
        self._recovering_messages = False

    def recover_pending_messages(self, thread_id: str) -> None:
        """Drain durable history-write intents before later canonical items.

        The history row and intent commit together. Item identity, ordinal
        and counters commit together in the domain store. A crash before the
        final acknowledgement simply replays the same identity on next read
        or write. Only new writes opt in; this is not historical backfill.
        """
        if self._recovering_messages:
            return
        from omniagents.core.session.history_db import (
            pending_projections,
            acknowledge_projection,
        )

        self._recovering_messages = True
        try:
            pending = pending_projections(
                thread_id, project_slug=self._project_slug, agent_slug=self._agent_slug
            )
            if pending and self._store.get_thread(thread_id) is None:
                raise RuntimeError("Pending projection has no canonical thread")
            for entry in pending:
                intent = entry["intent"]
                message = entry["message"]
                self.validate_projection(message, intent)
                if intent.get("kind") == "queued_message":
                    recorded = self.record_queued_message(
                        thread_id,
                        queue_id=intent["queue_id"],
                        role=message["role"],
                        content=message["content"],
                    )
                    self._settled(recorded)
                elif intent.get("kind") == "user_message":
                    recorded = self._append_item(
                        thread_id,
                        natural_key=intent["natural_key"],
                        kind=ItemKind.USER_MESSAGE,
                        turn_id=intent.get("run_id"),
                        status=ItemStatus.COMPLETED,
                        role="user",
                        content={"text": message["content"]},
                        source_ref={
                            "event": "send_user_message",
                            "run_id": intent.get("run_id"),
                        },
                    )
                    self._settled(recorded)
                else:
                    self.record_persisted_message(
                        thread_id, run_id=intent.get("run_id"), item=message
                    )
                acknowledge_projection(
                    thread_id,
                    entry["row_id"],
                    project_slug=self._project_slug,
                    agent_slug=self._agent_slug,
                )
        finally:
            self._recovering_messages = False

    @classmethod
    def validate_projection(cls, message: dict, intent: dict) -> None:
        kind = intent.get("kind")
        valid = intent.get("version") == 1
        if kind in ("queued_message", "user_message"):
            identity = intent.get(
                "queue_id" if kind == "queued_message" else "natural_key"
            )
            valid = valid and isinstance(identity, str) and bool(identity)
            valid = valid and isinstance(message.get("content"), str)
            valid = valid and message.get("role") in (
                ("user", "assistant", "system", "developer")
                if kind == "queued_message"
                else ("user",)
            )
        else:
            valid = valid and kind is None and cls.is_persisted_message(message)
        if not valid:
            raise RuntimeError("Unsupported pending history projection")

    @property
    def store(self) -> ConversationStore:
        """The shard this recorder writes to. Exposed so a caller that also
        needs to READ (the plan tools) does not have to re-resolve it and
        risk landing on a different shard."""
        return self._store

    def _settled(self, item: Optional[Item]) -> None:
        if item is None or self._on_settled is None:
            return
        try:
            self._on_settled(item)
        except Exception:
            logger.exception(
                "item settlement listener failed", extra={"item": item.item_id}
            )

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
                extra={"thread_id": thread_id},
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
        source: str | None = None,
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
            self.recover_pending_messages(thread_id)
            thread = self._store.get_thread(thread_id)
            assert thread is not None

            def update(current):
                changed = False
                if source is not None and current.source != source:
                    current.source = source
                    changed = True
                if user_id and current.user_id != user_id:
                    current.user_id = user_id
                    changed = True
                status = ThreadStatus.ARCHIVED if archived else ThreadStatus.ACTIVE
                if current.status is not status:
                    current.status = status
                    changed = True
                if changed:
                    current.updated_at = _now()

            thread = self._store.mutate_thread(thread_id, update)
            assert thread is not None
            return thread
        now = created_at if created_at is not None else _now()
        thread = Thread(
            thread_id=thread_id,
            source=source or "recorded",
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
        long_lived: bool = False,
    ) -> Optional[Item]:
        """Create the item if it is new; otherwise return the existing one.

        Creation is keyed on the derived id, so a re-delivered ``started``
        event (the same ``call_id`` arriving twice) never allocates a second
        ordinal — which is what keeps ``seq`` dense and the transcript
        free of phantom duplicates.
        """
        item_id = derive_item_id(thread_id, natural_key)
        self.recover_pending_messages(thread_id)
        existing = self._store.get_item(thread_id, item_id)
        if existing is not None:
            return existing
        if self._store.get_thread(thread_id) is None:
            self.ensure_thread(thread_id)
        now = _now()
        item = Item(
            item_id=item_id,
            thread_id=thread_id,
            turn_id=turn_id,
            seq=0,  # Assigned with the item/counters in one store transaction.
            kind=kind,
            status=status,
            role=role,
            created_at=now,
            updated_at=now,
            completed_at=now if status is not ItemStatus.STARTED else None,
            revision=0,
            content=_as_dict(content),
            source_ref=_as_dict(source_ref),
            long_lived=long_lived,
        )
        return self._store.append_item(item)

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

        def revise(existing):
            merged = {**existing.content, **content}
            if merged == existing.content:
                return existing
            return replace(
                existing,
                updated_at=_now(),
                revision=existing.revision + 1,
                content=merged,
            )

        return self._store.mutate_item(thread_id, item_id, revise)

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
        return self._store.mutate_item(
            thread_id,
            item_id,
            lambda current: current.with_transition(
                status=status, content=content, now=_now()
            ),
        )

    # -- turns ----------------------------------------------------------

    def open_turn(
        self,
        thread_id: str,
        turn_id: str,
        *,
        prompt: str | None,
        prompt_role: str | None,
        user_id: str | None = None,
        attachments: list | None = None,
    ) -> Turn:
        self.ensure_thread(thread_id, user_id=user_id)
        now = _now()
        role = prompt_role or "user"

        def start(thread, existing, ordinal):
            if existing is not None:
                return existing
            thread.turn_count += 1
            thread.updated_at = now
            if prompt and ordinal == 1 and role == "user" and not thread.title:
                thread.title = derive_title(prompt)
            return Turn(
                turn_id=turn_id,
                thread_id=thread_id,
                ordinal=ordinal,
                status=TurnStatus.RUNNING,
                created_at=now,
                updated_at=now,
                prompt=prompt,
                prompt_role=prompt_role,
            )

        initial_item = None
        if prompt:
            initial_item = Item(
                thread_id=thread_id,
                item_id=derive_item_id(thread_id, f"prompt:{turn_id}"),
                seq=0,
                created_at=now,
                updated_at=now,
                completed_at=now,
                kind=(
                    ItemKind.USER_MESSAGE if role == "user" else ItemKind.AGENT_MESSAGE
                ),
                turn_id=turn_id,
                status=ItemStatus.COMPLETED,
                role=role,
                content={
                    "text": prompt,
                    "origin": "prompt",
                    **({"attachments": attachments} if attachments else {}),
                },
                source_ref={"event": "run_started", "run_id": turn_id},
            )
        turn = self._store.mutate_turn(
            thread_id, turn_id, start, initial_item=initial_item
        )
        assert turn is not None
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
        self.recover_pending_messages(thread_id)
        status = terminal_status_for_end_reason(end_reason)
        now = _now()
        # Settle the run's diff snapshot BEFORE the orphan sweep. The
        # snapshot is deliberately turn-scoped and stays ``started`` while
        # further edits revise it, so without this it would be caught by the
        # sweep below and stamped ``failed`` at the end of every SUCCESSFUL
        # run. Landing it here — the owning subsystem finalizing its own
        # item, the same contract ``long_lived`` items sign — makes the
        # end state honest: the diff of a completed run is completed.
        self.settle_run_diff(
            thread_id, turn_id, status=_RUN_DIFF_TERMINAL[status], now=now
        )
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
            if item.kind == ItemKind.APPROVAL and item.content.get("worker_session_id"):
                # Forwarded worker approvals belong to the child task, not
                # whichever parent turn happens to be running when they arrive.
                # Its resolution event lands the item; runtime reconciliation
                # makes orphaned controls unavailable after process loss.
                continue
            if item.long_lived:
                # Deliberately outlives its turn; the owning subsystem
                # lands it terminally (see Item.long_lived).
                continue
            self._store.mutate_item(
                thread_id,
                item.item_id,
                lambda current: current.with_transition(
                    status=orphan_status,
                    content={
                        "finalized_by": "run_end",
                        "end_reason": end_reason,
                        **(
                            {
                                "execution_state": "uncertain",
                                "output": "Execution outcome unknown after interruption; do not assume it is safe to repeat.",
                                "metadata": {"display_type": "error"},
                            }
                            if current.content.get("execution_state") == "claimed"
                            else {}
                        ),
                    },
                    now=now,
                ),
            )

        def finish(thread, turn, ordinal):
            if turn is None or turn.status != TurnStatus.RUNNING:
                return None
            turn.status = status
            turn.end_reason = end_reason
            turn.error = error
            turn.completed_at = now
            turn.updated_at = now
            if usage:
                turn.usage = dict(usage)
            if model:
                turn.model = model
            if model_ref:
                turn.model_ref = model_ref
            thread.usage = _merge_usage(thread.usage, turn.usage)
            thread.updated_at = now
            return turn

        self._store.mutate_turn(thread_id, turn_id, finish)

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
        attachments = payload.get("attachments")
        self.open_turn(
            thread_id,
            run_id,
            prompt=prompt if isinstance(prompt, str) else None,
            prompt_role=(
                payload.get("prompt_role")
                if isinstance(payload.get("prompt_role"), str)
                else None
            ),
            attachments=attachments if isinstance(attachments, list) else None,
        )

    def _on_run_status(self, thread_id: str, payload: Dict[str, Any]) -> None:
        run_id = payload.get("run_id")
        if not isinstance(run_id, str):
            return

        def update(thread, turn, ordinal):
            if turn is None or turn.status != TurnStatus.RUNNING:
                return None
            attempt = payload.get("attempt")
            if isinstance(attempt, int):
                turn.attempts = max(turn.attempts, attempt)
            turn.updated_at = _now()
            return turn

        self._store.mutate_turn(thread_id, run_id, update)

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
        message_id = provider_item_id(message_id)
        if message_id:
            return f"message:{message_id}"
        return f"message:{turn_id}:{content_key(content)}:{occurrence}"

    @staticmethod
    def is_persisted_message(item: Dict[str, Any]) -> bool:
        from .adapter import _checkpoint_kind

        return (
            (item.get("type") or "message") == "message"
            and item.get("role") == "assistant"
            and item.get("status") == "completed"
            and isinstance(item.get("id"), str)
            and bool(item["id"])
            and _checkpoint_kind(item) is None
        )

    def record_persisted_message(
        self, thread_id: str, *, run_id: str | None, item: Dict[str, Any]
    ) -> Optional[Item]:
        """Record completed provider output independently of SDK event delivery.

        Stable provider identity lets persistence and notification converge in
        either order. Do not infer identity for injected prompts, checkpoints,
        partial output or ID-less messages: those have other recording paths.
        This runs at the history write, before a later checkpoint can land.
        """
        from .adapter import _message_text

        message_id = item.get("id")
        if not self.is_persisted_message(item):
            return None
        recorded = self._on_message_output(
            thread_id,
            {
                "run_id": run_id,
                "message_id": message_id,
                "content": _message_text(item.get("content")),
            },
        )
        # Publish even when the stream recorded it first: a client may have
        # missed that event. Repeated notifications carry the same canonical
        # identity/revision and are safe to adopt idempotently.
        self._settled(recorded)
        return recorded

    def _on_message_output(
        self, thread_id: str, payload: Dict[str, Any]
    ) -> Optional[Item]:
        run_id = payload.get("run_id")
        content = payload.get("content")
        # An empty assistant message (chat-completions providers send one
        # beside each tool call) carries nothing to record.
        if not isinstance(content, str) or not content.strip():
            return None
        turn_id = run_id if isinstance(run_id, str) else None
        message_id = payload.get("message_id")
        natural_key = (
            self.message_natural_key(turn_id, content, message_id)
            if isinstance(message_id, str) and message_id
            else self._next_occurrence_key(
                thread_id, f"message:{turn_id}:{content_key(content)}"
            )
        )
        return self._append_item(
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
            run_id = payload.get("run_id")
            if isinstance(run_id, str) and run_id:
                return f"tool:{content_key(run_id, call_id)}"
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
                # The tool's declared ``running_summary`` (bridge attaches it
                # as ``metadata.summary`` on ``tool_called``). Without it a
                # canonical-rendering client shows the raw tool name while
                # the call is in flight. Superseded wholesale by the result's
                # metadata in ``_on_tool_result``.
                **(
                    {"metadata": payload["metadata"]}
                    if isinstance(payload.get("metadata"), dict)
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
        # Written even when empty: the result's metadata supersedes the
        # call-time metadata (the running summary) WHOLESALE — a result
        # without metadata must not leave "Running…" phrasing on a
        # finished item.
        content["metadata"] = metadata

        # The invocation hook may have committed the result before the SDK
        # stream delivers richer UI metadata. Enrich only that same result;
        # never let replay change its output, owner, or terminal outcome.
        def enrich(current):
            if current.content.get("execution_state") == "claimed" and metadata.get(
                "execution_interrupted"
            ):
                return current.with_transition(
                    status=ItemStatus.FAILED,
                    content={
                        "execution_state": "uncertain",
                        "output": "Execution outcome unknown after interruption; do not assume it is safe to repeat.",
                        "metadata": metadata,
                    },
                    now=_now(),
                )
            if current.content.get(
                "execution_state"
            ) == "completed" and current.content.get("output") == payload.get("output"):
                return replace(
                    current,
                    content={
                        **current.content,
                        "metadata": {**current.content.get("metadata", {}), **metadata},
                    },
                    updated_at=_now(),
                )
            return current.with_transition(
                status=ItemStatus.FAILED if failed else ItemStatus.COMPLETED,
                content=content,
                now=_now(),
            )

        self._store.mutate_item(thread_id, derive_item_id(thread_id, key), enrich)

    def claim_tool_execution(self, thread_id: str, payload: dict, owner: str) -> Item:
        key = self._tool_natural_key(payload)
        if key is None or not payload.get("run_id"):
            raise ValueError("Tool execution requires run and call identities")
        self._on_tool_called(thread_id, payload)

        def claim(current):
            if current.is_terminal or current.content.get("execution_state"):
                raise RuntimeError(
                    "Tool dispatch already claimed; refusing duplicate execution"
                )
            if current.content.get("tool") != payload.get(
                "tool"
            ) or current.content.get("input") != payload.get("input"):
                raise ValueError("Tool call identity reused for different content")
            return replace(
                current,
                content={**current.content, "execution_state": "claimed"},
                source_ref={**current.source_ref, "execution_owner": owner},
                updated_at=_now(),
            )

        item = self._store.mutate_item(thread_id, derive_item_id(thread_id, key), claim)
        if item is None:
            raise RuntimeError("Tool dispatch was not durably recorded")
        return item

    def complete_tool_execution(
        self, thread_id: str, payload: dict, owner: str
    ) -> Item:
        key = self._tool_natural_key(payload)
        if key is None:
            raise ValueError("Tool completion requires a call identity")
        metadata = _as_dict(payload.get("metadata"))

        def complete(current):
            if current.source_ref.get("execution_owner") != owner:
                raise RuntimeError("Tool completion belongs to another dispatcher")
            if current.content.get("execution_state") == "completed":
                if current.content.get("output") != payload.get("output"):
                    raise ValueError(
                        "Tool completion conflicts with its durable result"
                    )
                return current
            content = {
                **current.content,
                "execution_state": "completed",
                "output": payload.get("output"),
                "metadata": metadata,
            }
            if current.is_terminal:
                # A cancelled turn may finish while its tool is winding down.
                # Retain the real result without changing the turn's outcome.
                return replace(current, content=content, updated_at=_now())
            return current.with_transition(
                status=(
                    ItemStatus.FAILED
                    if metadata.get("display_type") == "error"
                    else ItemStatus.COMPLETED
                ),
                content=content,
                now=_now(),
            )

        item = self._store.mutate_item(
            thread_id, derive_item_id(thread_id, key), complete
        )
        if item is None:
            raise RuntimeError("Tool completion has no durable dispatch")
        return item

    def _on_token(self, thread_id: str, payload: Dict[str, Any]) -> None:
        run_id = payload.get("run_id")
        if not isinstance(run_id, str):
            return

        def update(thread, turn, ordinal):
            if turn is None or turn.status != TurnStatus.RUNNING:
                return None
            totals = _as_dict(payload.get("totals"))
            if totals:
                turn.usage = dict(totals)
            for field_name in ("model", "model_ref"):
                value = payload.get(field_name)
                if isinstance(value, str) and value:
                    setattr(turn, field_name, value)
            turn.updated_at = _now()
            return turn

        self._store.mutate_turn(thread_id, run_id, update)

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
                "source_call_id": payload.get("source_call_id"),
                **{
                    key: payload[key]
                    for key in ("worker_id", "worker_session_id", "worker_run_id")
                    if key in payload
                },
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
                # ``decision`` is normally attached by
                # ``record_approval_decision`` (the wire event carries only
                # the correlation id today), but a resolution payload that
                # does carry it must not be recorded more blandly than it
                # arrived.
                key: payload[key]
                for key in ("reason", "decision")
                if payload.get(key) is not None
            },
        )

    def _on_tool_approval_reviewed(
        self, thread_id: str, payload: Dict[str, Any]
    ) -> None:
        """A reviewer-resolved approval (sandbox policy or guardian).

        ``kind`` selects the approval namespace: the tool lane keys on
        ``call_id``, and the hosted-MCP lane reuses the same payload field
        for its ``request_id`` (``request_mcp_approval`` passes
        ``call_id=request_id`` into the review path).

        The verdict is MERGED onto the canonical approval item so a
        reloading client renders the guardian's decision, not a bland
        record. But in every auto-resolved lane the prompt was never
        broadcast — ``_resolve_approval_without_client`` returns before
        ``tool_approval_requested`` goes out — so there is usually no item
        to merge into. The review is then appended as its own approval
        item, born terminal: the decision already happened, and dropping it
        would show a reloading client a transcript missing a verdict it
        saw live.
        """
        call_id = payload.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            return
        approval_kind = "mcp" if payload.get("kind") == "mcp" else "tool"
        natural_key = f"approval:{approval_kind}:{call_id}"
        verdict = {
            key: payload[key]
            for key in ("reviewer", "outcome", "rationale", "risk_level")
            if isinstance(payload.get(key), str)
        }
        if self._update_item(thread_id, natural_key, content=verdict) is not None:
            return
        tool_name = payload.get("tool_name")
        server_label = payload.get("server_label")
        self._append_item(
            thread_id,
            natural_key=natural_key,
            kind=ItemKind.APPROVAL,
            turn_id=(
                payload.get("run_id")
                if isinstance(payload.get("run_id"), str)
                else None
            ),
            status=ItemStatus.COMPLETED,
            content={
                "approval_kind": approval_kind,
                "tool": tool_name,
                "tool_kind": classify_tool(
                    tool_name if isinstance(tool_name, str) else None,
                    server_label=(
                        server_label if isinstance(server_label, str) else None
                    ),
                ).value,
                **(
                    {"server_label": server_label}
                    if isinstance(server_label, str)
                    else {}
                ),
                # Same identity field the matching *_approval_requested
                # handler would have written, so the two shapes agree.
                ("request_id" if approval_kind == "mcp" else "call_id"): call_id,
                "source_call_id": payload.get("source_call_id"),
                "arguments": payload.get("arguments"),
                **verdict,
            },
            source_ref={"event": "tool_approval_reviewed", "call_id": call_id},
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
                "source_request_id": payload.get("source_request_id"),
                **{
                    key: payload[key]
                    for key in ("worker_id", "worker_session_id", "worker_run_id")
                    if key in payload
                },
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

    def _on_plan_completion_reviewed(
        self, thread_id: str, payload: Dict[str, Any]
    ) -> None:
        """A workflow completion-review verdict (``task_list_tools``).

        Append-only, with the occurrence ordinal restoring distinctness —
        the same task is legitimately reviewed more than once (rejected,
        then accepted), and the transcript is a record: collapsing the
        rejection into the later accept would hide why the run took the
        path it did. Born terminal for the same reason as
        :attr:`ItemKind.WORKFLOW_REVIEW` documents: it is the record of a
        decision, not a lifecycle.
        """
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            return
        run_id = payload.get("run_id")
        self._append_item(
            thread_id,
            natural_key=self._next_occurrence_key(
                thread_id, f"workflow_review:{task_id}"
            ),
            kind=ItemKind.WORKFLOW_REVIEW,
            turn_id=run_id if isinstance(run_id, str) else None,
            status=ItemStatus.COMPLETED,
            role="assistant",
            content={
                "task_id": task_id,
                **{
                    key: payload[key]
                    for key in ("subject", "outcome", "reviewer", "rationale")
                    if isinstance(payload.get(key), str)
                },
            },
            source_ref={"event": "plan_completion_reviewed", "task_id": task_id},
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
        # A fork-from-turn carries its branch point on the notification.
        # It has to: this handler is how a fork is materialized, so a
        # branch point known only to the RPC handler would produce a child
        # holding the parent's ENTIRE transcript — the fork would silently
        # not be a branch. The wire value is the item id (a protocol
        # concept); the ordinal it resolves to is internal.
        up_to_seq: int | None = None
        from_item_id = payload.get("from_item_id")
        if isinstance(from_item_id, str) and from_item_id:
            branch_item = self._store.get_item(thread_id, from_item_id)
            if branch_item is None:
                logger.warning(
                    "fork branch point not found; refusing to materialize an "
                    "untruncated child",
                    # NOT ``thread``: that is a reserved LogRecord
                    # attribute and logging raises on the collision.
                    extra={"thread_id": thread_id, "item_id": from_item_id},
                )
                return
            up_to_seq = branch_item.seq
        self.record_fork(thread_id, new_thread_id, up_to_seq=up_to_seq)

    # -- explicit (non-notification) entry points -------------------------

    def record_fork(
        self,
        parent_thread_id: str,
        new_thread_id: str,
        *,
        up_to_seq: int | None = None,
    ) -> None:
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

        ``up_to_seq`` branches from a point *inside* the parent instead of
        its tail: only items with ``seq <= up_to_seq`` are copied, turns are
        clipped to the items that came with them, and a turn left with no
        items is not copied at all. Two invariants make that safe:

        * ``last_seq`` is set from the HIGHEST COPIED ordinal, not from the
          parent's cursor. The child's next ``allocate_seq`` therefore
          returns one past its own last row. Inheriting the parent's cursor
          would leave a gap (harmless); inheriting a *truncated* view of it
          would hand out an ordinal an existing row already holds, and the
          unique ``(thread_id, seq)`` index turns that into a lost item.
        * ``branched_from_item_id`` is the last copied item, so the branch
          point a client renders is the one the child actually ends at.

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

        source_items = self._store.list_items(
            parent_thread_id,
            limit=_FORK_MAX_ITEMS,
            # ``before_seq`` is strict, so the branch point itself is
            # included by asking for everything below the next ordinal.
            before_seq=None if up_to_seq is None else up_to_seq + 1,
        )
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
        copied_items = [self._fork_item(new_thread_id, item) for item in source_items]
        items_by_turn: Dict[str, list[Item]] = {}
        for item in copied_items:
            if item.turn_id is not None:
                items_by_turn.setdefault(item.turn_id, []).append(item)
        copied_turns = [
            self._fork_turn(new_thread_id, turn, items_by_turn.get(turn.turn_id, []))
            for turn in source_turns
            # A turn whose items all sit above the branch point did not
            # happen in the child's history; copying its header would show
            # an empty turn the child never ran.
            if turn.turn_id in items_by_turn
        ]
        child.title = parent.title
        # The child's ordinal cursor is its OWN high-water mark.
        child.last_seq = max(item.seq for item in copied_items)
        child.turn_count = len(copied_turns)
        child.item_count = len(copied_items)
        child.usage = dict(parent.usage)
        child.compaction = self._fork_compaction(parent.compaction, child.last_seq)
        child.source = parent.source
        # The copy IS the child's materialization.
        child.projected_at = _now()
        self._store.write_projection(child, copied_turns, copied_items)

    @staticmethod
    def _fork_compaction(compaction: Dict[str, Any], last_seq: int) -> Dict[str, Any]:
        """The parent's compaction floor, dropped if it is above the branch.

        A checkpoint the child does not contain cannot be its context floor;
        carrying the parent's value would tell a client that items the child
        *does* hold are outside the model's live context.
        """
        checkpoint_seq = compaction.get("checkpoint_seq")
        if isinstance(checkpoint_seq, int) and checkpoint_seq > last_seq:
            return {}
        return dict(compaction)

    @staticmethod
    def _fork_turn(new_thread_id: str, turn: Turn, items: list[Item]) -> Turn:
        """One parent turn, re-addressed under the forked thread.

        ``items`` are the turn's COPIED items. On an untruncated fork they
        are all of them and the counters are unchanged; on a fork-from-turn
        the trailing ones were cut, so ``item_count``/``first_seq``/
        ``last_seq`` are recomputed from what actually landed rather than
        inherited — a turn advertising a ``last_seq`` past the end of the
        thread is a keyset window a client would page into and find empty.
        """
        seqs = [item.seq for item in items]
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
            item_count=len(seqs),
            first_seq=min(seqs) if seqs else None,
            last_seq=max(seqs) if seqs else None,
            attempts=turn.attempts,
            source=turn.source,
            schema_version=turn.schema_version,
        )

    @staticmethod
    def _forked_item_id(new_thread_id: str, item: Item) -> str:
        """The child's id for a copied parent item.

        Normally a fresh derivation from the parent's id, which keeps the
        two transcripts independently addressable. A PLAN is the exception:
        its id is not an opaque handle, it is
        ``derive_item_id(thread, "plan:<scope>[#gen]")`` and the plan store
        LOOKS IT UP by that key. Re-addressing a plan the generic way left
        the child's plan at an id ``get_plan`` could not find — so the child
        read back "no plan", and its first ``task_create`` minted a SECOND
        open plan in the same scope which, being ``long_lived``, no sweep
        would ever close.
        """
        if item.kind is ItemKind.PLAN:
            from .plans import plan_natural_key

            scope = item.content.get("scope")
            generation = item.content.get("generation")
            return derive_item_id(
                new_thread_id,
                plan_natural_key(
                    scope if isinstance(scope, str) and scope else "main",
                    generation if isinstance(generation, int) else 0,
                ),
            )
        return derive_item_id(new_thread_id, f"forked:{item.item_id}")

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
            item_id=ConversationRecorder._forked_item_id(new_thread_id, item),
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
        """A model reasoning block, recorded when the run streams it.

        Reasoning has no notification, so the bridge records it directly
        from the SDK's stream event. That event arrives before the tool
        calls the reasoning led to, which keeps the transcript in the order
        the model produced it. The SDK writes the same block to history
        only after those tools ran; see :meth:`record_persisted_reasoning`.
        """
        def joined(entries: Any) -> str:
            parts: list[str] = []
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, str):
                        parts.append(entry)
                    elif isinstance(entry, dict) and isinstance(entry.get("text"), str):
                        parts.append(entry["text"])
            return "\n".join(parts)

        # The Responses API summarizes reasoning; chat-completions providers
        # return it raw, as ``reasoning_text`` content with no summary.
        text = joined(item.get("summary")) or joined(item.get("content"))
        reasoning_id = provider_item_id(item.get("id"))
        key = (
            f"reasoning:{reasoning_id}"
            if reasoning_id
            else self._next_occurrence_key(
                thread_id, f"reasoning:{run_id}:{content_key(text)}"
            )
        )
        recorded = self._append_item(
            thread_id,
            natural_key=key,
            kind=ItemKind.REASONING,
            turn_id=run_id,
            status=ItemStatus.COMPLETED,
            role="assistant",
            content={
                "summary": text,
                **({"reasoning_id": reasoning_id} if reasoning_id else {}),
            },
            source_ref={"event": "reasoning", "run_id": run_id},
        )
        self._settled(recorded)
        return recorded

    def record_persisted_reasoning(
        self, thread_id: str, *, run_id: str | None, item: Dict[str, Any]
    ) -> Optional[Item]:
        """Record reasoning from the history write, when it is identifiable.

        Covers a block whose stream event was never delivered. Only a
        provider-minted id lets the two paths converge on one item; an
        id-less block (chat-completions providers) is recorded by the
        stream path alone, as id-less assistant messages are.
        """
        if not provider_item_id(item.get("id")):
            return None
        return self.record_reasoning(thread_id, run_id=run_id, item=item)

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

    def record_queued_message(
        self, thread_id: str, *, queue_id: str, role: str, content: str
    ) -> Optional[Item]:
        """Record an append-only queue item, outside any completed run.

        Queue identity makes retries idempotent without collapsing distinct
        injections that happen to contain the same text.
        """
        return self._append_item(
            thread_id,
            natural_key=f"queue:{queue_id}",
            kind=ItemKind.USER_MESSAGE if role == "user" else ItemKind.AGENT_MESSAGE,
            turn_id=None,
            status=ItemStatus.COMPLETED,
            role=role,
            content={"text": content},
            source_ref={"event": "enqueue_message", "queue_id": queue_id},
        )

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

    def commit_reviewed_approval(self, thread_id: str, payload: dict) -> Item:
        """Persist an automatic verdict before it may authorize execution."""
        if payload.get("kind") not in ("tool", "mcp") or payload.get("outcome") not in (
            "allow",
            "deny",
        ):
            raise ValueError("Invalid automatic approval decision")
        token = payload.get("call_id")
        if not isinstance(token, str) or not token:
            raise ValueError("Automatic approval requires an identity")
        self._on_tool_approval_reviewed(thread_id, payload)
        item = self._store.get_item(
            thread_id, derive_item_id(thread_id, f"approval:{payload['kind']}:{token}")
        )
        if item is None or item.status != ItemStatus.COMPLETED:
            raise RuntimeError("Automatic approval was not durably recorded")
        for key in ("reviewer", "outcome", "rationale", "arguments", "source_call_id"):
            if item.content.get(key) != payload.get(key):
                raise ValueError(
                    "Automatic approval identity already has a different decision"
                )
        return item

    def commit_approval_decision(
        self, thread_id: str, *, approval_kind: str, resource_id: str, response: dict
    ) -> Item:
        """Durable first-writer-wins decision, required before releasing a waiter.

        Exact retries are safe even if commit succeeded but its acknowledgement
        was lost. Missing/retired prompts fail closed; never invent authorization.
        """
        if approval_kind not in ("tool", "mcp") or response.get("decision") not in (
            "approve",
            "reject",
        ):
            raise ValueError("Invalid approval decision")
        frozen = json.loads(json.dumps(response))

        def update(current):
            previous = current.content.get("approval_response")
            if previous is not None:
                if previous != frozen:
                    raise ValueError(
                        "Approval already resolved with a different decision"
                    )
                return current
            if current.status != ItemStatus.STARTED:
                raise RuntimeError("Approval request is no longer pending")
            if (
                "decision" in current.content
                and current.content["decision"] != frozen["decision"]
            ):
                raise ValueError("Approval already resolved with a different decision")
            return current.with_transition(
                status=ItemStatus.COMPLETED,
                content={**frozen, "approval_response": frozen},
                now=_now(),
            )

        item = self._store.mutate_item(
            thread_id,
            derive_item_id(thread_id, f"approval:{approval_kind}:{resource_id}"),
            update,
        )
        if item is None:
            raise RuntimeError(
                "Approval request is not durably recorded; retry the decision"
            )
        return item

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

        def update(thread):
            # An older concurrent checkpoint must not move the floor back.
            if thread.compaction.get("checkpoint_seq", 0) >= item.seq:
                return
            thread.compaction = {
                "checkpoint_item_id": item.item_id,
                "checkpoint_seq": item.seq,
                "compacted_below_seq": item.seq,
            }
            thread.updated_at = _now()

        self._store.mutate_thread(thread_id, update)
        return item

    def record_archived(self, thread_id: str, archived: bool) -> None:
        def update(thread):
            thread.status = ThreadStatus.ARCHIVED if archived else ThreadStatus.ACTIVE
            thread.updated_at = _now()

        if self._store.mutate_thread(thread_id, update) is None:
            return
        if archived:
            # Archiving is the end of the conversation, which is the end of
            # anything that outlives its turns. Without this the thread's
            # plan would stay ``started`` forever and the "no in-flight
            # state once a conversation is done" guarantee would hold for
            # every item kind except the one that opted out of the sweep.
            self.finalize_plans(thread_id, reason="thread_archived")

    # -- plans (tkt_FqUypyxF2tzC) -----------------------------------------

    def record_plan(
        self,
        thread_id: str,
        *,
        natural_key: str,
        content: Dict[str, Any],
        run_id: str | None = None,
    ) -> Optional[Item]:
        """Create-or-update the durable plan item for one ``(thread, scope)``.

        Created ``long_lived`` because a plan accumulates steps across many
        turns: without the exemption ``run_end`` would stamp it terminal at
        the close of the very first run and every later ``task_update``
        would be silently refused by the terminal-once rule.

        ``turn_id`` is stamped from the run that FIRST wrote the plan and is
        not moved afterwards, so a turn-scoped read still surfaces the plan
        where it originated; the plan's own ``updated_at``/``revision`` carry
        recency.
        """
        item_id = derive_item_id(thread_id, natural_key)
        if self._store.get_item(thread_id, item_id) is None:
            return self._append_item(
                thread_id,
                natural_key=natural_key,
                kind=ItemKind.PLAN,
                turn_id=run_id,
                status=ItemStatus.STARTED,
                role="assistant",
                content=content,
                source_ref={"event": "plan_tool", "run_id": run_id},
                long_lived=True,
            )
        return self._update_item(thread_id, natural_key, content=content)

    def finalize_plans(self, thread_id: str, *, reason: str) -> list[Item]:
        """Land every open plan on *thread_id* terminally. Returns the items.

        ``completed`` only when the plan actually finished — at least one
        step and every step ``completed``. Anything else is ``cancelled``:
        stamping a plan with three pending steps as "completed" because the
        conversation was archived would make the historical record lie about
        work that never happened.
        """
        now = _now()
        landed: list[Item] = []
        for item in self._store.open_items(thread_id):
            if item.kind is not ItemKind.PLAN:
                continue

            def finalize(current):
                steps = current.content.get("steps")
                steps = steps if isinstance(steps, list) else []
                finished = bool(steps) and all(
                    isinstance(step, dict) and step.get("status") == "completed"
                    for step in steps
                )
                return current.with_transition(
                    status=ItemStatus.COMPLETED if finished else ItemStatus.CANCELLED,
                    content={"finalized_by": reason},
                    now=now,
                )

            updated = self._store.mutate_item(thread_id, item.item_id, finalize)
            if updated is None:
                continue
            self._settled(updated)
            landed.append(updated)
        return landed

    # -- run diffs (tkt_FqUypyxF2tzC) -------------------------------------

    @staticmethod
    def run_diff_natural_key(run_id: str) -> str:
        return f"run_diff:{run_id}"

    def record_run_diff(
        self, thread_id: str, *, run_id: str, snapshot: Dict[str, Any]
    ) -> Optional[Item]:
        """Create-or-revise the aggregated diff snapshot for one run.

        Called after every observed workspace mutation, so the item holds a
        live view of the run's net change rather than a summary written once
        at the end — a run that is cancelled halfway still leaves a complete,
        readable record of what it had done by then.
        """
        key = self.run_diff_natural_key(run_id)
        if self._store.get_item(thread_id, derive_item_id(thread_id, key)) is None:
            return self._append_item(
                thread_id,
                natural_key=key,
                kind=ItemKind.RUN_DIFF,
                turn_id=run_id,
                status=ItemStatus.STARTED,
                role="assistant",
                content={"run_id": run_id, **snapshot},
                source_ref={"event": "workspace_mutation", "run_id": run_id},
            )
        updated = self._update_item(thread_id, key, content=snapshot)
        if updated is not None and updated.is_terminal:
            # The run already ended (a late mutation, or a tool that wrote
            # after run_end). ``_update_item`` refuses to move a terminal
            # item's CONTENT, so revise it explicitly — the diff of a run is
            # a fact about the workspace, and dropping a real change would
            # make the aggregate incomplete.
            return self._revise_item(thread_id, key, content=snapshot)
        return updated

    def settle_run_diff(
        self,
        thread_id: str,
        turn_id: str,
        *,
        status: ItemStatus = ItemStatus.COMPLETED,
        now: float | None = None,
    ) -> Optional[Item]:
        """Land this turn's diff snapshot terminally.

        ``status`` mirrors how the RUN ended, so a cancelled run's diff is
        ``cancelled`` and a failed run's is ``failed``. Hard-coding
        ``completed`` made the diff the one item of a cancelled turn claiming
        it finished — the same lie ``finalize_plans`` refuses to tell about a
        half-finished plan.
        """
        key = self.run_diff_natural_key(turn_id)
        settled = self._store.mutate_item(
            thread_id,
            derive_item_id(thread_id, key),
            lambda current: current.with_transition(
                status=status, now=now if now is not None else _now()
            ),
        )
        self._settled(settled)
        return settled


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


def push_item_updated(session: Any, item: Item, service: Any = None) -> None:
    """Schedule an ``item_updated`` push for *item*, best-effort.

    Fire-and-forget by design: the item is already durable when this runs, so
    a missing push degrades to "the client's next read is authoritative",
    never to a failed write. Silent when there is no service, no session, or
    no running loop (the recorder is also driven directly from tests).

    ``service`` is passed explicitly by callers that are NOT inside a run.
    ``current_service()`` is a contextvar set on the run's task tree, so it
    resolves during a turn but is ``None`` in an RPC handler like
    ``archive_session`` — which is exactly where a plan lands terminally.
    Relying on the contextvar alone made that push silently never happen.
    """
    if session is None:
        return
    try:
        import asyncio

        from omniagents.core.agents.service import current_service

        service = service or current_service()
        if service is None:
            return
        asyncio.ensure_future(service.notify_item_updated(session, item))
    except Exception:
        logger.debug("item_updated push unavailable", exc_info=True)


def recorder_for_session(
    session: Any, *, service: Any = None
) -> Optional[ConversationRecorder]:
    """Build a recorder bound to *session*'s (project, agent) shard.

    The single construction chokepoint for recorders that have a live
    session, so wiring the settlement listener here is what makes terminal
    landings push ``item_updated`` from every path — the ``run_end`` sweep,
    the archive sweep, and the plan tools alike.
    """
    project_slug = getattr(session, "_project_slug", None)
    agent_slug = getattr(session, "_agent_slug", None)
    try:
        store = get_conversation_store(project_slug, agent_slug)
    except Exception:
        logger.exception("conversation store unavailable; domain objects not recorded")
        return None
    return ConversationRecorder(
        store,
        project_slug=project_slug,
        agent_slug=agent_slug,
        on_settled=lambda item: push_item_updated(session, item, service),
    )


def _session_thread_source(session: Any) -> str | None:
    context = getattr(session, "context", None)
    return "worker" if isinstance(context, dict) and context.get("worker_id") else None


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
        source=_session_thread_source(session),
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
            source=_session_thread_source(session),
        )
        recorder.record(session_id, method, payload)
    except Exception:
        logger.exception("conversation recording failed", extra={"method": method})
