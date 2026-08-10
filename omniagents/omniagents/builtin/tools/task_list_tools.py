"""Structured plan tools, backed by the durable plan item.

These tools used to hold the agent's plan in two module-level dicts keyed
by ``task_scope`` — whose default value is the constant ``"main"``. That
made the plan a *process singleton*: every session served by one
``omniagents serve`` process wrote into the same list, so two concurrent
conversations clobbered each other, a restart erased the plan, and a
reconnecting client could only recover whatever ``tasks_snapshot`` still
happened to be attached to a retained ``tool_result``.

The state now lives in a canonical ``plan`` item keyed on
``(thread_id, scope)`` (:mod:`omniagents.core.conversation.plans`), where
``thread_id`` is the session — including for a worker sub-agent, which runs
under its OWN session and therefore its own thread. ``scope`` separates
plans within one thread and is ``"main"`` throughout the current topology.
The
tool surface — names, arguments, return shapes, and the ``tasks_snapshot``
side-channel the web UI and Ink TUI already read — is unchanged, so this is
a replacement of the storage, not a second parallel system and not a client
break.

Sessionless fallback. Tools invoked outside a session (direct ``Runner``
use, unit tests) have no thread to persist into. Those runs fall back to a
process-local dictionary keyed by ``task_scope`` — the old behaviour,
restricted to the only case where it is not a bug, since a run with no
session cannot collide with another *session*. Every framework-managed run
carries ``session_id`` and therefore gets the durable path.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from agents.run_context import RunContextWrapper

from omniagents.core.conversation.recorder import push_item_updated
from omniagents.core.conversation.plans import (
    PLAN_STEP_STATUSES,
    Plan,
    PlanGenerationsExhausted,
    PlanStep,
    PlanStore,
    dependency_map,
    link,
    unlink,
    unlink_all,
    would_cycle,
)
from omniagents.core.tools import (
    RichToolOutput,
    rich_function_tool,
    session_id_from_ctx,
    task_scope_from_ctx,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from omniagents.core.conversation.recorder import ConversationRecorder

logger = logging.getLogger(__name__)

#: Statuses the agent may set, plus the pseudo-status that removes a step.
_VALID_STATUSES = PLAN_STEP_STATUSES
_DELETED = "deleted"

#: Sessionless plans, keyed by ``task_scope``. See the module docstring:
#: this is the pre-existing behaviour kept for runs that have no session,
#: NOT the path a served agent takes.
_SESSIONLESS_PLANS: Dict[str, Plan] = {}


def _status_emoji(status: str) -> str:
    return {
        "pending": "·",
        "in_progress": "▶",
        "completed": "✓",
        "blocked": "⨯",
    }.get(status, "?")


class _PlanHandle:
    """Read/write access to one run's plan, durable or sessionless."""

    def __init__(
        self, thread_id: Optional[str], scope: str, session: Any = None
    ) -> None:
        self.thread_id = thread_id
        self.scope = scope
        self.session = session
        self._store: Optional[PlanStore] = None
        self._recorder: Optional["ConversationRecorder"] = None

    # -- resolution ------------------------------------------------------

    def _durable(self) -> bool:
        return self.thread_id is not None

    def _bind(self) -> bool:
        """Attach a store + recorder for the durable path. False on failure.

        A storage fault degrades to the sessionless dict rather than failing
        the agent's tool call — the same best-effort posture the conversation
        recorder takes, and the reason is the same: plan bookkeeping must
        never abort a user's turn.
        """
        if self._store is not None:
            return True
        if self.thread_id is None:
            return False
        try:
            from omniagents.core.conversation.recorder import (
                ConversationRecorder,
                recorder_for_session,
            )
            from omniagents.core.conversation.store import get_conversation_store

            # Shard on the session's (project, agent) exactly as the
            # recorder does. Reading the default shard instead would put a
            # project-scoped agent's plan in a different database from its
            # own transcript. Going through ``recorder_for_session`` also
            # picks up the settlement listener, so a plan landed terminally
            # from here pushes ``item_updated`` like every other path.
            recorder = recorder_for_session(self.session) if self.session else None
            if recorder is None:
                project_slug = getattr(self.session, "_project_slug", None)
                agent_slug = getattr(self.session, "_agent_slug", None)
                recorder = ConversationRecorder(
                    get_conversation_store(project_slug, agent_slug),
                    project_slug=project_slug,
                    agent_slug=agent_slug,
                )
            self._recorder = recorder
            self._store = PlanStore(recorder.store)
            recorder.ensure_thread(
                self.thread_id,
                user_id=getattr(self.session, "user_id", None),
                archived=bool(getattr(self.session, "archived", False)),
            )
            return True
        except Exception:
            logger.exception("durable plan unavailable; using sessionless fallback")
            self._store = None
            self._recorder = None
            return False

    # -- reads -----------------------------------------------------------

    def read(self) -> Plan:
        if self._durable() and self._bind():
            assert self._store is not None and self.thread_id is not None
            return self._store.current(self.thread_id, self.scope) or self._store.empty(
                self.thread_id, self.scope
            )
        plan = _SESSIONLESS_PLANS.get(self.scope)
        if plan is None:
            plan = Plan(
                plan_id=f"plan_local_{self.scope}",
                thread_id="",
                scope=self.scope,
                generation=0,
            )
            _SESSIONLESS_PLANS[self.scope] = plan
        return plan

    # -- writes ----------------------------------------------------------

    def write(self, plan: Plan) -> Plan:
        """Persist *plan* and return the stored view (revision advanced)."""
        if not (self._durable() and self._bind()):
            _SESSIONLESS_PLANS[self.scope] = plan
            return plan
        assert self._recorder is not None and self._store is not None
        assert self.thread_id is not None
        # Re-resolve the generation at write time. The plan read at the top
        # of the tool call could have been finalized in between (the session
        # was archived mid-run), and writing to a terminal item is a silent
        # no-op — the agent would be told its edit landed when it did not.
        key, generation = self._store.current_key(self.thread_id, self.scope)
        plan.generation = generation
        plan.plan_id = self._store.plan_id(self.thread_id, self.scope, generation)
        item = self._recorder.record_plan(
            self.thread_id,
            natural_key=key,
            content=plan.to_content(),
            run_id=_current_run_id(),
        )
        if item is None:
            return plan
        stored = Plan.from_item(item, scope=self.scope, generation=generation)
        push_item_updated(self.session, item)
        return stored


def _current_run_id() -> Optional[str]:
    """The run this tool call belongs to, if any.

    The runtime names the run's asyncio task after its ``run_id`` (see
    ``core/runtime/bridge.py``), which is how every other in-run producer
    correlates without threading the id through each call site.
    """
    import asyncio

    try:
        task = asyncio.current_task()
    except RuntimeError:
        return None
    return task.get_name() if task is not None else None


def _handle(ctx: RunContextWrapper[Any] | None) -> _PlanHandle:
    from omniagents.core.session.helpers import session_from_ctx

    session = None
    try:
        session = session_from_ctx(ctx)
    except Exception:
        session = None
    return _PlanHandle(session_id_from_ctx(ctx), task_scope_from_ctx(ctx), session)


def _snapshot(plan: Plan) -> List[Dict[str, Any]]:
    return plan.to_summaries()


def _plan_metadata(plan: Plan) -> Dict[str, Any]:
    """Identity fields every plan tool result carries.

    A client that wants the authoritative object (rather than the compact
    snapshot) needs the plan's address, and it must be on every result so a
    client that joined mid-run can find it from the first event it sees.
    """
    return {"plan_id": plan.plan_id, "scope": plan.scope, "revision": plan.revision}


def _step_payload(plan: Plan, step: PlanStep) -> Dict[str, Any]:
    """The step as returned to the model.

    Deliberately the PRE-EXISTING wire shape of these tools — camelCase
    ``activeForm`` / ``blockedBy``, not the snake_case of the persisted item
    — plus one additive field. The tools' result JSON is a contract with
    every agent prompt and transcript that already references it, and with
    the ``tasks_snapshot`` side-channel next to it in the same payload;
    renaming half of it while the snapshot kept the old names would have
    made one tool result internally inconsistent for no gain.

    ``blockedBy`` is the declared edge set; ``openBlockers`` is the subset
    that is not yet completed. Both are structured — the model (and any
    client) reads state instead of inferring it from prose.
    """
    return {
        "id": step.id,
        "subject": step.subject,
        "description": step.description,
        "activeForm": step.active_form or step.subject,
        "status": step.status,
        "owner": step.owner,
        "blocks": list(step.blocks),
        "blockedBy": list(step.blocked_by),
        "openBlockers": plan.open_blockers(step),
        "exitCriteria": step.exit_criteria,
        "criteriaEdited": bool(step.original_exit_criteria),
        "originalExitCriteria": step.original_exit_criteria,
    }


def _exhausted_error(handle: "_PlanHandle", attempted: Plan) -> RichToolOutput:
    """The plan could not be written because every generation is terminal.

    Returned instead of a success payload: telling the agent an edit landed
    when the store refused it is the one failure mode a plan tool must never
    have.

    ``attempted`` is the caller's in-memory plan, already carrying the edit
    the store refused; it is deliberately NOT what gets rendered.
    ``tasks_snapshot`` drives the docked task panel, so building it from
    ``attempted`` would put the step that was never stored into the very
    payload that reports the failure — the same lie, moved one field over.
    """
    plan = handle.read()  # the STORED plan, not `attempted`
    return _error(
        plan,
        summary="Plan could not be updated",
        preview=(
            "Every plan generation for this scope has been finalized; the "
            "edit was not stored."
        ),
        payload={"error": "plan_generations_exhausted", "scope": plan.scope},
    )


def _error(
    plan: Plan, *, summary: str, preview: str, payload: Dict[str, Any]
) -> RichToolOutput:
    llm_output = json.dumps(payload)
    return RichToolOutput(
        llm_output,
        {
            "display_type": "error",
            "summary": summary,
            "preview": preview,
            "truncated": False,
            "hidden": True,
            "tasks_snapshot": _snapshot(plan),
            "metadata": {"success": False, **_plan_metadata(plan), **payload},
        },
    )


# ---------------------------------------------------------------------------
# Completion review — semantic guard on ``status="completed"``
# ---------------------------------------------------------------------------


async def _review_completion(
    handle: "_PlanHandle",
    plan: Plan,
    step: PlanStep,
    criteria: str,
    original_criteria: str,
) -> tuple[Optional[Dict[str, Any]], Optional[RichToolOutput]]:
    """Route one ``completed`` attempt through completion review.

    Returns ``(review, denial)`` — exactly one is non-None. ``review`` is
    the record to stamp on the step before the write; ``denial`` is the
    error output to return with nothing written. *criteria* is the
    EFFECTIVE exit criteria for this call (an ``exitCriteria`` edit in the
    same ``task_update`` applies) and *original_criteria* the EFFECTIVE
    ratchet record (including one stamped by this same call), while *step*
    stays untouched so a denial's ``tasks_snapshot`` renders the stored
    plan.

    Fail-open-to-allow throughout: no service, no session, judge timeout,
    or malformed judge output all accept the completion as ``unverified``.
    Plan bookkeeping must never abort a turn (module docstring rule).
    """
    from dataclasses import replace

    from omniagents.core.agents import plan_review
    from omniagents.core.agents.workflow_policy import (
        COMPLETION_ROUTE_OFF,
        effective_workflow_reviewer,
        resolve_completion_route,
    )

    service = _service()
    session = handle.session
    reviewer = effective_workflow_reviewer(service, session)
    route = resolve_completion_route(reviewer, replace(step, exit_criteria=criteria))

    if route == COMPLETION_ROUTE_OFF:
        return {"outcome": "waived", "reviewer": "none", "rationale": ""}, None

    run_id = _current_run_id()

    # GUARDIAN — codex-style: accept completes (verified or unverified),
    # reject returns the rationale to the model with nothing written.
    if plan_review.breaker_should_accept(session, run_id, step.id):
        last = plan_review.last_rejection_rationale(session, run_id, step.id)
        rationale = f"accepted after repeated disagreement: {last}"
        review = {
            "outcome": "unverified",
            "reviewer": "guardian",
            "rationale": rationale,
        }
        plan_review.record_accept(session, run_id, step.id)
        await _broadcast_completion_review(
            handle,
            task_id=step.id,
            subject=step.subject,
            outcome="escalated",
            reviewer="guardian",
            rationale=rationale,
            run_id=run_id,
        )
        return review, None

    request = plan_review.PlanReviewRequest(
        step_id=step.id,
        subject=step.subject,
        description=step.description,
        exit_criteria=criteria,
        original_exit_criteria=original_criteria,
        plan_id=plan.plan_id,
        scope=plan.scope,
    )
    assessment = await plan_review.review_completion(service, session, request)

    if assessment is None:
        review = {
            "outcome": "unverified",
            "reviewer": "none",
            "rationale": "review unavailable",
        }
        plan_review.record_accept(session, run_id, step.id)
        await _broadcast_completion_review(
            handle,
            task_id=step.id,
            subject=step.subject,
            outcome="accept_unverified",
            reviewer="none",
            rationale="review unavailable",
            run_id=run_id,
        )
        return review, None

    if assessment.accepted:
        plan_review.record_accept(session, run_id, step.id)
        outcome = "verified" if assessment.verified else "unverified"
        review = {
            "outcome": outcome,
            "reviewer": "guardian",
            "rationale": assessment.rationale,
        }
        await _broadcast_completion_review(
            handle,
            task_id=step.id,
            subject=step.subject,
            outcome=(
                "accept_verified" if outcome == "verified" else "accept_unverified"
            ),
            reviewer="guardian",
            rationale=assessment.rationale,
            run_id=run_id,
        )
        return review, None

    plan_review.record_rejection(session, run_id, step.id, assessment.rationale)
    await _broadcast_completion_review(
        handle,
        task_id=step.id,
        subject=step.subject,
        outcome="reject",
        reviewer="guardian",
        rationale=assessment.rationale,
        run_id=run_id,
    )
    denial = _error(
        plan,
        summary=f"Task #{step.id} completion rejected",
        preview=f"Exit criteria not met: {assessment.rationale}"[:1500],
        payload={
            "task_id": step.id,
            "error": "exit_criteria_not_met",
            "rationale": assessment.rationale,
        },
    )
    return None, denial


def _service() -> Any:
    """The AgentService owning this run's task tree, or ``None``."""
    try:
        from omniagents.core.agents.service import current_service

        return current_service()
    except Exception:
        return None


async def _broadcast_completion_review(
    handle: "_PlanHandle",
    *,
    task_id: str,
    subject: str,
    outcome: str,
    reviewer: str,
    rationale: str,
    run_id: Optional[str],
) -> None:
    """Broadcast one non-clean completion-review outcome.

    Emitted for every REVIEWED outcome — rejections, unverified accepts,
    clean verifications (``accept_verified``), and breaker/human
    escalations. Only waived completions (no criteria / reviewer off) stay
    silent: nothing was reviewed, so there is no decision to record. Same mechanism as
    ``tool_approval_reviewed``: session-scoped, queued if unseen.
    Best-effort — a broadcast failure never fails the tool call.
    """
    service = _service()
    session = handle.session
    if service is None or session is None:
        return
    payload: Dict[str, Any] = {
        "session_id": getattr(session, "id", None) or handle.thread_id,
        "task_id": task_id,
        "subject": subject,
        "outcome": outcome,
        "reviewer": reviewer,
        "rationale": rationale,
    }
    if run_id is not None:
        payload["run_id"] = run_id
    try:
        await service._broadcast_session(
            session,
            "plan_completion_reviewed",
            payload,
            queue_if_unseen=True,
        )
    except Exception:
        logger.exception("plan_completion_reviewed broadcast failed")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@rich_function_tool(running_summary="Creating task...")
async def task_create(
    ctx: RunContextWrapper[Any],
    subject: str,
    description: str,
    activeForm: Optional[str] = None,
    exitCriteria: Optional[str] = None,
) -> RichToolOutput:
    """Create a new task in your task list.

    Use proactively for multi-step work (≥3 non-trivial steps). Skip for
    single trivial actions or purely conversational requests.

    Args:
        subject: Short imperative title, e.g. "Run the test suite".
        description: What the task entails. Include enough context that
            anyone (including a subagent that picks it up) can execute
            without re-deriving the requirements.
        activeForm: Present-continuous form shown in the spinner while the
            task is in_progress, e.g. "Running the test suite".
            Falls back to ``subject`` if omitted.
        exitCriteria: Definition-of-done for the task. State a criterion
            an outside reviewer could check from the transcript: a
            command that must have run and passed, a file that must
            exist, a PR that must be open. Phrase it as evidence, not
            intent ("`npm test` passes", not "code is tested").
            Completing a task that has exitCriteria is reviewed and may
            be rejected with a rationale; tasks without it complete
            unreviewed.
    """
    handle = _handle(ctx)
    plan = handle.read()
    step = PlanStep(
        id=plan.next_step_id(),
        subject=subject,
        description=description,
        active_form=activeForm or subject,
        status="pending",
        exit_criteria=exitCriteria or "",
    )
    plan.steps.append(step)
    try:
        plan = handle.write(plan)
    except PlanGenerationsExhausted:
        return _exhausted_error(handle, plan)
    stored = plan.step(step.id) or step

    llm_output = json.dumps({"task": _step_payload(plan, stored)})
    return RichToolOutput(
        llm_output,
        {
            "display_type": "command",
            "summary": f"Task #{stored.id}: {subject[:60]}",
            "preview": f"#{stored.id} [pending] {subject}",
            "truncated": False,
            "hidden": True,
            "tasks_snapshot": _snapshot(plan),
            "metadata": {
                "success": True,
                "task_id": stored.id,
                "status": stored.status,
                **_plan_metadata(plan),
            },
        },
    )


@rich_function_tool(running_summary="Listing tasks...")
async def task_list(ctx: RunContextWrapper[Any]) -> RichToolOutput:
    """List your tasks with status and open blockers.

    Returns summaries only — call ``task_get`` for full description.
    """
    handle = _handle(ctx)
    plan = handle.read()
    tasks = _snapshot(plan)
    counts = plan.counts()

    llm_output = json.dumps({"tasks": tasks, "counts": counts})

    if not tasks:
        preview = "(no tasks)"
    else:
        preview = "\n".join(
            f"{_status_emoji(t['status'])} #{t['id']} {t['subject'][:80]}"
            + (f"  [blocked by {','.join(t['blockedBy'])}]" if t["blockedBy"] else "")
            for t in tasks
        )

    summary_parts = [f"{n} {s}" for s, n in counts.items() if n]
    summary = ", ".join(summary_parts) if summary_parts else "no tasks"

    return RichToolOutput(
        llm_output,
        {
            "display_type": "search_results",
            "summary": f"Tasks: {summary}",
            "preview": preview,
            "truncated": False,
            "hidden": True,
            "tasks_snapshot": tasks,
            "metadata": {
                "success": True,
                **counts,
                "total": len(tasks),
                **_plan_metadata(plan),
            },
        },
    )


@rich_function_tool(running_summary="Reading task...")
async def task_get(ctx: RunContextWrapper[Any], task_id: str) -> RichToolOutput:
    """Get the full record for one of your tasks by id.

    Args:
        task_id: The task id returned by task_create.
    """
    handle = _handle(ctx)
    plan = handle.read()
    step = plan.step(task_id)
    if step is None:
        return _error(
            plan,
            summary=f"Task #{task_id} not found",
            preview=f"Task #{task_id} not found",
            payload={"task_id": task_id, "error": "not_found"},
        )

    payload = _step_payload(plan, step)
    llm_output = json.dumps({"task": payload})
    preview = f"#{step.id} [{step.status}] {step.subject}\n\n{step.description}"
    return RichToolOutput(
        llm_output,
        {
            "display_type": "command",
            "summary": f"Task #{task_id}: {step.subject[:60]}",
            "preview": preview[:1500],
            "truncated": len(preview) > 1500,
            "hidden": True,
            "tasks_snapshot": _snapshot(plan),
            "metadata": {
                "success": True,
                "task_id": task_id,
                "status": step.status,
                **_plan_metadata(plan),
            },
        },
    )


@rich_function_tool(running_summary="Updating task...")
async def task_update(
    ctx: RunContextWrapper[Any],
    task_id: str,
    status: Optional[str] = None,
    subject: Optional[str] = None,
    description: Optional[str] = None,
    activeForm: Optional[str] = None,
    owner: Optional[str] = None,
    addBlocks: Optional[list[str]] = None,
    addBlockedBy: Optional[list[str]] = None,
    removeBlockedBy: Optional[list[str]] = None,
    exitCriteria: Optional[str] = None,
    overrideBlockers: bool = False,
) -> RichToolOutput:
    """Update a task. Only fields you pass are changed.

    Mark a task ``in_progress`` BEFORE starting work and ``completed``
    immediately after finishing — that's what drives the UI status. Only
    one task should be ``in_progress`` at a time. Don't mark completed
    if the work is partial: set ``blocked`` and say what you are waiting
    on, or leave it in_progress and create a task for the blocker.

    Setting ``in_progress`` on a task whose blockers are still open is
    refused with error ``blocked`` listing the open blocker ids —
    complete the blockers first, or pass ``overrideBlockers=true``.
    Setting ``completed`` on a task that has ``exitCriteria`` is
    reviewed against the transcript and may be rejected with error
    ``exit_criteria_not_met`` and a rationale; address the rationale
    (or correct the criteria), then mark it completed again.
    A dependency edge that would create a cycle is refused with error
    ``dependency_cycle`` — a cycle would deadlock every task on it.

    Args:
        task_id: The task to update.
        status: ``pending`` | ``in_progress`` | ``completed`` | ``blocked``,
            or ``deleted`` to permanently remove the task.
        subject: New title.
        description: New description.
        activeForm: New present-continuous form for the spinner.
        owner: Claim the task by setting an owner string (e.g. agent
            name).
        addBlocks: Append task ids that this task blocks. Their
            ``blockedBy`` lists are updated symmetrically. An edge that
            would create a dependency cycle is refused with error
            ``dependency_cycle`` naming the existing chain — restructure
            the tasks or remove an edge with ``removeBlockedBy`` first.
        addBlockedBy: Append task ids that must complete before this
            one starts. Symmetric edge written into the dependency's
            ``blocks`` list. Same cycle refusal as ``addBlocks``.
        removeBlockedBy: Remove dependency edges: this task no longer
            waits on these task ids (their ``blocks`` lists are updated
            symmetrically). The ordinary fix for a mis-drawn edge —
            prefer this over ``overrideBlockers``. Absent edges are
            ignored.
        exitCriteria: New definition-of-done for the task — a criterion
            an outside reviewer could check from the transcript (a
            command that must have run and passed, a file that must
            exist, a PR that must be open). Pass an empty string to
            clear it. Editing the criteria of a task that is already
            in_progress is allowed but recorded: completion review sees
            both the edited and the original criteria.
        overrideBlockers: Start the task even though blockers are open.
            Use only when a blocker is stale or is being handled
            elsewhere.
    """
    handle = _handle(ctx)
    plan = handle.read()
    step = plan.step(task_id)
    if step is None:
        return _error(
            plan,
            summary=f"Task #{task_id} not found",
            preview=f"Task #{task_id} not found",
            payload={"task_id": task_id, "error": "not_found"},
        )

    if status == _DELETED:
        unlink_all(plan, task_id)
        plan.steps = [entry for entry in plan.steps if entry.id != task_id]
        try:
            plan = handle.write(plan)
        except PlanGenerationsExhausted:
            return _exhausted_error(handle, plan)
        llm_output = json.dumps({"task_id": task_id, "status": _DELETED})
        return RichToolOutput(
            llm_output,
            {
                "display_type": "command",
                "summary": f"Task #{task_id} deleted",
                "preview": llm_output,
                "truncated": False,
                "hidden": True,
                "tasks_snapshot": _snapshot(plan),
                "metadata": {
                    "success": True,
                    "task_id": task_id,
                    "status": _DELETED,
                    **_plan_metadata(plan),
                },
            },
        )

    if status is not None and status not in _VALID_STATUSES:
        return _error(
            plan,
            summary=f"Invalid status: {status}",
            preview=f"Valid: {', '.join(_VALID_STATUSES)} or '{_DELETED}'",
            payload={"task_id": task_id, "error": f"invalid status: {status}"},
        )

    # Both guards run BEFORE any mutation: a denial writes nothing, and the
    # error payload's ``tasks_snapshot`` must render the STORED plan, not
    # the edit that was refused.
    if status == "in_progress" and not overrideBlockers:
        open_blockers = plan.open_blockers(step)
        if open_blockers:
            names = ", ".join(
                f"#{blocker_id}"
                + (f" ({other.subject})" if (other := plan.step(blocker_id)) else "")
                for blocker_id in open_blockers
            )
            return _error(
                plan,
                summary=f"Task #{task_id} is blocked",
                preview=(
                    f"Blocked by {names}. Complete the blockers first, or "
                    "pass overrideBlockers=true."
                ),
                payload={
                    "task_id": task_id,
                    "error": "blocked",
                    "openBlockers": open_blockers,
                },
            )

    # Criteria-edit ratchet: an ``exitCriteria`` edit on a step that is
    # ``in_progress`` — or arriving in the same call that sets
    # ``completed`` — records the pre-edit value, once (the FIRST original
    # survives later edits). Edits while ``pending``/``blocked`` are free:
    # refining an unstarted step is planning, not weakening. The edit is
    # never blocked and fires no review of its own — the record is weighed
    # at completion review. Computed here (against the step's PRE-call
    # status) but stamped only after the guards, so a denial writes
    # nothing and its ``tasks_snapshot`` renders the stored plan.
    stamp_original = (
        exitCriteria is not None
        and exitCriteria != step.exit_criteria
        and (step.status == "in_progress" or status == "completed")
        and not step.original_exit_criteria
    )
    effective_original = (
        step.exit_criteria if stamp_original else step.original_exit_criteria
    )

    # Edge changes are validated BEFORE the completion review — a cycle
    # refusal must not leave review side effects (breaker resets,
    # broadcasts) behind — and before any mutation, so the error's
    # ``tasks_snapshot`` renders the stored plan. The simulation applies
    # removals first (a call may remove the very edge that would have made
    # its addition cyclic) and additions incrementally (two additions in
    # one call can form a cycle only with each other).
    new_edges = [(task_id, other_id) for other_id in addBlocks or []] + [
        (other_id, task_id) for other_id in addBlockedBy or []
    ]
    if new_edges:
        deps = dependency_map(plan)
        removed = set(removeBlockedBy or [])
        if removed and task_id in deps:
            deps[task_id] = [d for d in deps[task_id] if d not in removed]
        for blocker_id, blocked_id in new_edges:
            if plan.step(blocker_id) is None or plan.step(blocked_id) is None:
                continue  # link() ignores unknown ids
            cycle = would_cycle(deps, blocker_id, blocked_id)
            if cycle is not None:
                chain = " → ".join(f"#{sid}" for sid in cycle)
                return _error(
                    plan,
                    summary=f"Task #{task_id}: dependency cycle refused",
                    preview=(
                        f"#{blocked_id} cannot wait on #{blocker_id}: "
                        f"#{blocker_id} already waits on #{blocked_id} "
                        f"({chain}, where → means 'waits on'). Restructure "
                        "the tasks or remove an edge with removeBlockedBy."
                    ),
                    payload={
                        "task_id": task_id,
                        "error": "dependency_cycle",
                        "edge": {"blocker": blocker_id, "blocked": blocked_id},
                        "path": cycle,
                    },
                )
            deps.setdefault(blocked_id, []).append(blocker_id)

    review: Optional[Dict[str, Any]] = None
    if status == "completed":
        criteria = exitCriteria if exitCriteria is not None else step.exit_criteria
        review, denial = await _review_completion(
            handle, plan, step, criteria, effective_original
        )
        if denial is not None:
            return denial

    if stamp_original:
        step.original_exit_criteria = effective_original
    if status is not None:
        step.status = status
    if subject is not None:
        step.subject = subject
    if description is not None:
        step.description = description
    if activeForm is not None:
        step.active_form = activeForm
    if owner is not None:
        step.owner = owner
    if exitCriteria is not None:
        step.exit_criteria = exitCriteria
    if review is not None:
        step.review = review

    for other_id in removeBlockedBy or []:
        unlink(plan, other_id, task_id)
    for blocker_id, blocked_id in new_edges:
        # Pre-validated above; link() independently refuses cycles too, so
        # persisted content can't hold one even if the simulation and the
        # plan ever disagree.
        link(plan, blocker_id, blocked_id)

    try:
        plan = handle.write(plan)
    except PlanGenerationsExhausted:
        return _exhausted_error(handle, plan)
    stored = plan.step(task_id) or step
    llm_output = json.dumps({"task": _step_payload(plan, stored)})
    return RichToolOutput(
        llm_output,
        {
            "display_type": "command",
            "summary": f"Task #{task_id} → {stored.status}",
            "preview": f"#{task_id} [{stored.status}] {stored.subject}",
            "truncated": False,
            "hidden": True,
            "tasks_snapshot": _snapshot(plan),
            "metadata": {
                "success": True,
                "task_id": task_id,
                "status": stored.status,
                **_plan_metadata(plan),
            },
        },
    )


def reset_sessionless_plans() -> None:
    """Clear the sessionless fallback. Test-fixture entry point."""
    _SESSIONLESS_PLANS.clear()


__all__ = [
    "reset_sessionless_plans",
    "task_create",
    "task_get",
    "task_list",
    "task_update",
]
