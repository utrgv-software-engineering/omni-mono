"""Plan-completion reviewer — a guardian-style judge for exit criteria.

When a plan step carrying non-empty ``exit_criteria`` is marked
``completed`` and the workflow reviewer is ``guardian``
(:mod:`omniagents.core.agents.workflow_policy`), the completion routes
here instead of landing directly. The reviewer:

1. Builds the same compact, token-budgeted transcript the security
   guardian uses (:mod:`omniagents.core.agents.transcript_compaction`).
2. Runs a bounded agentic judge (:func:`run_agent_once`, turn budget
   :data:`PLAN_REVIEW_MAX_TURNS`) on the configured model against the
   shipped completion policy (``plan_review_policy.md``), asking for a
   strict JSON assessment of whether the step's exit criteria are met.
   When the calling run has a bound workspace, the judge carries
   READ-ONLY workspace tools (:func:`reviewer_tools`) that execute
   through the same ``omniagents.workspace`` contextvar the doer's
   tools use — so inspection happens in the doer's exact environment,
   host or sandbox alike. With no bound workspace (sessionless / test
   contexts) it degrades to the one-shot, tool-less judgment.
3. **Fails open to allow** — never to the human — on timeout, execution
   failure, or malformed output: the completion is accepted as
   ``unverified``. This is the deliberate posture difference from the
   security guardian (which fails to the human): the stake here is
   process discipline, not damage, and plan bookkeeping must never
   abort a turn.

Outcome routing is codex-style: ``accept`` completes the step (recorded
``verified`` or ``unverified`` depending on the evidence); ``reject``
returns the rationale to the model as a tool error and leaves the step
untouched. A per-``(session, run, step)`` circuit breaker stops
re-rejecting the same step after :data:`MAX_CONSECUTIVE_REJECTIONS`
consecutive rejections — the next attempt is accepted ``unverified``
rather than parking the run in a disagreement loop.

Reviews are serialized per session so parallel tool-call batches assess
coherently and cost stays bounded.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from omniagents.core.agents.transcript_compaction import (
    MAX_ACTION_CHARS,
    clip,
    compact_transcript,
)

logger = logging.getLogger(__name__)

PLAN_REVIEW_TIMEOUT_SECONDS = 120.0
#: Turn budget for the agentic review run — enough for a handful of
#: read-only workspace inspections plus the final JSON verdict.
PLAN_REVIEW_MAX_TURNS = 8
#: Consecutive rejections of the SAME step after which the breaker stops
#: rejecting: the next ``completed`` attempt is accepted ``unverified``.
MAX_CONSECUTIVE_REJECTIONS = 2

CRITERIA_MET = ("satisfied", "unsatisfied", "indeterminate")
EVIDENCE = ("shown", "asserted", "absent")
OUTCOMES = ("accept", "reject")

_POLICY_PATH = Path(__file__).with_name("plan_review_policy.md")


@dataclass(frozen=True)
class PlanReviewAssessment:
    criteria_met: str
    evidence: str
    outcome: str
    rationale: str

    @property
    def accepted(self) -> bool:
        return self.outcome == "accept"

    @property
    def verified(self) -> bool:
        """Accepted with the criteria demonstrably met from tool output."""
        return (
            self.outcome == "accept"
            and self.criteria_met == "satisfied"
            and self.evidence == "shown"
        )


@dataclass(frozen=True)
class PlanReviewRequest:
    """The exact step completion under review."""

    step_id: str
    subject: str
    description: str
    exit_criteria: str
    #: Pre-edit criteria recorded by the criteria-edit ratchet when
    #: ``exit_criteria`` was changed while the step was in progress.
    #: Empty means the criteria were never edited mid-step.
    original_exit_criteria: str = ""
    plan_id: Optional[str] = None
    scope: Optional[str] = None


def plan_review_policy_text() -> str:
    return _POLICY_PATH.read_text(encoding="utf-8")


def reviewer_tools() -> list:
    """The reviewer's READ-ONLY workspace toolset.

    File reading and searching only — no write, edit, execute, or web
    tools. The tool bodies route every filesystem access through the
    ``omniagents.workspace`` contextvar, which the calling run bound to
    its environment lease (host or sandbox), so the reviewer inspects
    exactly the filesystem the doer's tools acted on. Execution tools
    are deliberately absent: a criterion that needs a command run is
    satisfied only by the doer's own run appearing in the transcript.
    """
    from omniagents.builtin.tools.file_tools import (
        glob_files,
        grep_files,
        list_directory,
        read_file,
    )

    return [read_file, list_directory, glob_files, grep_files]


def _bound_workspace_root() -> Optional[str]:
    """Root of the workspace bound to the current async context, or ``None``.

    ``review_completion`` is awaited inside the doer's ``task_update``
    tool call, beneath the run's environment lease (``stream_agent``
    binds the lease's workspace to the contextvar before the run), so a
    bound workspace here IS the doer's execution environment — the
    reviewer's tools inherit it through the contextvar. ``None`` (no
    lease: sessionless or test contexts) routes the review tool-less.
    """
    from omniagents.workspace import current_workspace

    try:
        root = current_workspace().root
    except Exception:
        return None
    if isinstance(root, str) and root.strip():
        return root
    return None


async def review_completion(
    service: Any,
    session: Any,
    request: PlanReviewRequest,
    *,
    timeout: float = PLAN_REVIEW_TIMEOUT_SECONDS,
) -> Optional[PlanReviewAssessment]:
    """Assess *request*; ``None`` means "review unavailable" (accept
    unverified — the caller fails open to allow, never to the human).

    Never raises: every failure mode (no service, model error, timeout,
    malformed JSON, unresolvable model) collapses to ``None``.
    """
    if service is None:
        return None
    lock = _session_review_lock(session)
    try:
        async with lock:
            return await asyncio.wait_for(
                _review(service, session, request), timeout=timeout
            )
    except asyncio.TimeoutError:
        logger.warning(
            "plan-review: review timed out (%.0fs) for step %s; accepting unverified",
            timeout,
            request.step_id,
        )
        return None
    except Exception:
        logger.exception(
            "plan-review: review failed for step %s; accepting unverified",
            request.step_id,
        )
        return None


def _resolve_judge_model(service: Any, session: Any) -> tuple[Optional[str], Any, Any]:
    """``(model_name, model_provider, env_overrides)`` for the judge run.

    ``workflow.guardian_model`` when configured, else the session's own
    model via the utility-agent preference order — the same resolution
    the security guardian performs, against the workflow config key.
    """
    from omniagents.core.agents.workflow_policy import workflow_guardian_model_ref
    from omniagents.core.models import (
        normalize_model_name,
        provider_from_runtime,
        resolve_model_runtime,
        resolve_service_model_runtime,
    )

    configured = workflow_guardian_model_ref(service)
    if configured:
        model_name, runtime = resolve_model_runtime(configured)
    else:
        model_ref, runtime = resolve_service_model_runtime(service, session)
        model_name = normalize_model_name(model_ref, runtime)
    if not model_name:
        return None, None, None
    model_provider, env_overrides = provider_from_runtime(runtime)
    return model_name, model_provider, env_overrides


async def _review(
    service: Any, session: Any, request: PlanReviewRequest
) -> Optional[PlanReviewAssessment]:
    from omniagents.core.agents.specs import AgentSpec
    from omniagents.core.runtime.ephemeral import run_agent_once

    model_name, model_provider, env_overrides = _resolve_judge_model(service, session)
    if not model_name:
        logger.warning("plan-review: no resolvable model; accepting unverified")
        return None

    prompt = await _build_prompt(service, session, request)
    # Bounded agentic run when the doer's environment is reachable:
    # read-only tools execute through the inherited workspace contextvar,
    # and the run context is rooted where the doer's tools are rooted.
    workspace_root = _bound_workspace_root()
    tools = reviewer_tools() if workspace_root else []
    spec = AgentSpec(
        name="plan-review",
        welcome_text="",
        get_agent_instructions=plan_review_policy_text,
        build_agent=None,
        available_tools=tools,
    )
    raw = await run_agent_once(
        spec,
        prompt,
        model=model_name,
        model_provider=model_provider,
        env_overrides=env_overrides,
        session=session,
        workspace_root=workspace_root,
        max_turns=PLAN_REVIEW_MAX_TURNS if tools else 1,
    )
    return parse_assessment(raw)


def parse_assessment(raw: Any) -> Optional[PlanReviewAssessment]:
    """Strict parse of the reviewer's JSON output; ``None`` on any deviation."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    # Tolerate a fenced code block around the JSON, nothing more.
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    criteria_met = data.get("criteria_met")
    evidence = data.get("evidence")
    outcome = data.get("outcome")
    rationale = data.get("rationale")
    if (
        criteria_met not in CRITERIA_MET
        or evidence not in EVIDENCE
        or outcome not in OUTCOMES
        or not isinstance(rationale, str)
        or not rationale.strip()
    ):
        return None
    return PlanReviewAssessment(
        criteria_met=criteria_met,
        evidence=evidence,
        outcome=outcome,
        rationale=rationale.strip(),
    )


# ---------------------------------------------------------------------------
#  Circuit breaker — per (session, run, step): after
#  MAX_CONSECUTIVE_REJECTIONS consecutive rejections of the SAME step, the
#  next completion attempt is accepted unverified. The counter resets on
#  accept and whenever a different step is reviewed.
# ---------------------------------------------------------------------------

#: Breaker state for sessionless runs (no Session object to hang state
#: on) — the same fallback posture ``task_list_tools`` takes for the plan
#: itself. Test fixtures clear it via :func:`reset_sessionless_breaker`.
_SESSIONLESS_BREAKER: Dict[str, Any] = {}


def record_rejection(
    session: Any, run_id: Optional[str], step_id: str, rationale: str
) -> None:
    state = _breaker_state(session, run_id, step_id)
    state["consecutive"] = int(state.get("consecutive", 0)) + 1
    state["last_rationale"] = rationale


def record_accept(session: Any, run_id: Optional[str], step_id: str) -> None:
    state = _breaker_state(session, run_id, step_id)
    state["consecutive"] = 0
    state["last_rationale"] = ""


def breaker_should_accept(session: Any, run_id: Optional[str], step_id: str) -> bool:
    state = _breaker_state(session, run_id, step_id)
    return int(state.get("consecutive", 0)) >= MAX_CONSECUTIVE_REJECTIONS


def last_rejection_rationale(session: Any, run_id: Optional[str], step_id: str) -> str:
    state = _breaker_state(session, run_id, step_id)
    rationale = state.get("last_rationale")
    return rationale if isinstance(rationale, str) else ""


def reset_sessionless_breaker() -> None:
    """Clear the sessionless breaker fallback. Test-fixture entry point."""
    _SESSIONLESS_BREAKER.clear()


def _breaker_state(session: Any, run_id: Optional[str], step_id: str) -> dict:
    key = run_id or "__no_run__"
    state = (
        getattr(session, "_plan_review_breaker", None)
        if session is not None
        else _SESSIONLESS_BREAKER.get("state")
    )
    if (
        not isinstance(state, dict)
        or state.get("run_id") != key
        or state.get("step_id") != step_id
    ):
        # A new run or a DIFFERENT step resets the counter: the breaker
        # exists to break a disagreement loop over one step, not to cap
        # rejections across the plan.
        state = {
            "run_id": key,
            "step_id": step_id,
            "consecutive": 0,
            "last_rationale": "",
        }
        if session is not None:
            try:
                session._plan_review_breaker = state
            except Exception:
                _SESSIONLESS_BREAKER["state"] = state
        else:
            _SESSIONLESS_BREAKER["state"] = state
    return state


# ---------------------------------------------------------------------------
#  Prompt assembly
# ---------------------------------------------------------------------------


async def _build_prompt(service: Any, session: Any, request: PlanReviewRequest) -> str:
    transcript = await compact_transcript(service, session)
    step: dict[str, Any] = {
        "step_id": request.step_id,
        "subject": clip(request.subject, MAX_ACTION_CHARS),
        "description": clip(request.description, MAX_ACTION_CHARS),
        "exit_criteria": clip(request.exit_criteria, MAX_ACTION_CHARS),
    }
    if request.original_exit_criteria:
        # Criteria were edited while the step was in progress — show the
        # judge both versions, clearly labeled (policy: "Criteria edited
        # mid-step").
        step["exit_criteria_note"] = (
            "the exit criteria were edited while the step was in progress; "
            "exit_criteria above is the CURRENT (edited) version"
        )
        step["original_exit_criteria_before_mid_step_edit"] = clip(
            request.original_exit_criteria, MAX_ACTION_CHARS
        )
    if request.plan_id:
        step["plan_id"] = request.plan_id
    if request.scope:
        step["scope"] = request.scope
    return (
        "## Conversation transcript (most recent last)\n"
        f"{transcript or '(no prior transcript)'}\n\n"
        "## Plan step whose completion is under review\n"
        f"{json.dumps(step, ensure_ascii=False, default=str)}\n"
    )


def _session_review_lock(session: Any) -> asyncio.Lock:
    lock = getattr(session, "_plan_review_lock", None)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        try:
            session._plan_review_lock = lock
        except Exception:
            pass
    return lock
