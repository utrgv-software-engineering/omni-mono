"""Guardian reviewer — an LLM stand-in for the human at approval time.

When a session's approval reviewer is ``auto`` ("Approve for me"), pending
tool/MCP approvals route here instead of prompting the user. The guardian:

1. Builds a compact, token-budgeted transcript that preserves user intent
   plus recent assistant/tool context.
2. Runs a one-shot, tool-less utility agent (:func:`run_agent_once`) on the
   configured guardian model against the shipped risk policy
   (``guardian_policy.md``), asking for a strict JSON assessment of the
   exact planned action.
3. Fails to the human — never closed — on timeout, execution failure, or
   malformed output: the caller falls through to the ordinary prompt.

Outcome routing is codex-style: ``allow`` approves; ``deny`` rejects the
call back to the model with the rationale. A per-run circuit breaker stops
auto-denying after :data:`MAX_CONSECUTIVE_DENIALS` consecutive denials and
routes the rest of that run's approvals to the human.

Reviews are serialized per session so parallel tool-call batches assess
coherently and cost stays bounded.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from omniagents.core.agents.transcript_compaction import (
    MAX_ACTION_CHARS,
    MAX_MESSAGE_ENTRY_CHARS,
    MAX_TOOL_ENTRY_CHARS,
    MAX_TRANSCRIPT_CHARS,
    MAX_TRANSCRIPT_ENTRIES,
)
from omniagents.core.agents.transcript_compaction import clip as _clip
from omniagents.core.agents.transcript_compaction import (
    compact_transcript as _compact_transcript,
)
from omniagents.core.agents.transcript_compaction import (
    content_text as _content_text,
)
from omniagents.core.agents.transcript_compaction import (
    transcript_line as _transcript_line,
)

logger = logging.getLogger(__name__)

GUARDIAN_TIMEOUT_SECONDS = 45.0
MAX_CONSECUTIVE_DENIALS = 3

RISK_LEVELS = ("low", "medium", "high", "critical")
USER_AUTHORIZATIONS = ("unknown", "low", "medium", "high")
OUTCOMES = ("allow", "deny")

# Transcript budgets and compaction helpers live in
# ``transcript_compaction`` (shared with the plan-completion reviewer).
# The aliased imports above keep the historical module-level names
# (``_compact_transcript``, ``_clip``, ...) importable from here.

_POLICY_PATH = Path(__file__).with_name("guardian_policy.md")


@dataclass(frozen=True)
class GuardianAssessment:
    risk_level: str
    user_authorization: str
    outcome: str
    rationale: str

    @property
    def allowed(self) -> bool:
        return self.outcome == "allow"


@dataclass(frozen=True)
class GuardianRequest:
    """The exact planned action under review."""

    kind: str  # "tool" | "mcp"
    tool_name: str
    arguments: str
    server_label: Optional[str] = None
    metadata: Optional[dict] = None
    environment_kind: Optional[str] = None
    environment_has_network: bool = False


def guardian_policy_text() -> str:
    return _POLICY_PATH.read_text(encoding="utf-8")


async def review_approval(
    service: Any,
    session: Any,
    request: GuardianRequest,
    *,
    timeout: float = GUARDIAN_TIMEOUT_SECONDS,
) -> Optional[GuardianAssessment]:
    """Assess *request*; ``None`` means "route to the human".

    Never raises: every failure mode (model error, timeout, malformed JSON,
    unresolvable model) collapses to ``None`` so the caller falls through to
    the ordinary approval prompt.
    """
    lock = _session_review_lock(session)
    try:
        async with lock:
            return await asyncio.wait_for(
                _review(service, session, request), timeout=timeout
            )
    except asyncio.TimeoutError:
        logger.warning(
            "guardian: review timed out (%.0fs) for %s; routing to human",
            timeout,
            request.tool_name,
        )
        return None
    except Exception:
        logger.exception(
            "guardian: review failed for %s; routing to human", request.tool_name
        )
        return None


async def _review(
    service: Any, session: Any, request: GuardianRequest
) -> Optional[GuardianAssessment]:
    from omniagents.core.agents.approval_policy import guardian_model_ref
    from omniagents.core.agents.specs import AgentSpec
    from omniagents.core.models import (
        normalize_model_name,
        provider_from_runtime,
        resolve_model_runtime,
        resolve_service_model_runtime,
    )
    from omniagents.core.runtime.ephemeral import run_agent_once

    configured = guardian_model_ref(service)
    if configured:
        model_name, runtime = resolve_model_runtime(configured)
    else:
        model_ref, runtime = resolve_service_model_runtime(service, session)
        model_name = normalize_model_name(model_ref, runtime)
    if not model_name:
        logger.warning("guardian: no resolvable model; routing to human")
        return None
    model_provider, env_overrides = provider_from_runtime(runtime)
    model_settings = None
    if model_provider is None and not configured:
        # An agent that declares its provider in its own spec (YAML
        # ``model_provider: {type: openai-oauth}``) isn't in the model store,
        # so the runtime above carries no provider. Review on the agent's own
        # provider and settings rather than falling through to the SDK
        # default, which has no credentials for it (and, for Codex OAuth,
        # would also send requests the backend rejects without its settings).
        model_provider, model_settings = _spec_provider(service)
        if model_provider is not None:
            env_overrides = {}

    prompt = await _build_prompt(service, session, request)
    spec = AgentSpec(
        name="guardian",
        welcome_text="",
        get_agent_instructions=guardian_policy_text,
        build_agent=None,
        model_settings=model_settings,
    )
    raw = await run_agent_once(
        spec,
        prompt,
        model=model_name,
        model_provider=model_provider,
        env_overrides=env_overrides,
        session=session,
        max_turns=1,
    )
    return parse_assessment(raw)


def _spec_provider(service: Any) -> tuple[Any, Optional[dict]]:
    """The provider and model settings an agent spec declares for itself."""
    spec = getattr(service, "spec", None)
    config = getattr(spec, "model_provider_config", None)
    if not config:
        return None, None
    from omniagents.core.agents.run_options import build_model_provider

    try:
        provider = build_model_provider(config)
    except Exception:
        logger.exception("guardian: could not build the agent's model provider")
        return None, None
    if provider is None:
        return None, None
    settings = getattr(spec, "model_settings", None)
    return provider, dict(settings) if isinstance(settings, dict) else None


def parse_assessment(raw: str) -> Optional[GuardianAssessment]:
    """Strict parse of the guardian's JSON output; ``None`` on any deviation."""
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
    risk = data.get("risk_level")
    authorization = data.get("user_authorization")
    outcome = data.get("outcome")
    rationale = data.get("rationale")
    if (
        risk not in RISK_LEVELS
        or authorization not in USER_AUTHORIZATIONS
        or outcome not in OUTCOMES
        or not isinstance(rationale, str)
        or not rationale.strip()
    ):
        return None
    return GuardianAssessment(
        risk_level=risk,
        user_authorization=authorization,
        outcome=outcome,
        rationale=rationale.strip(),
    )


# ---------------------------------------------------------------------------
#  Circuit breaker — per (session, run): consecutive guardian denials route
#  the remainder of the run's approvals to the human.
# ---------------------------------------------------------------------------


def record_denial(session: Any, run_id: Optional[str]) -> None:
    state = _breaker_state(session, run_id)
    state["consecutive"] = int(state.get("consecutive", 0)) + 1
    if state["consecutive"] >= MAX_CONSECUTIVE_DENIALS:
        state["tripped"] = True


def record_allow(session: Any, run_id: Optional[str]) -> None:
    state = _breaker_state(session, run_id)
    state["consecutive"] = 0


def breaker_tripped(session: Any, run_id: Optional[str]) -> bool:
    state = _breaker_state(session, run_id)
    return bool(state.get("tripped"))


def _breaker_state(session: Any, run_id: Optional[str]) -> dict:
    state = getattr(session, "_guardian_breaker", None)
    key = run_id or "__no_run__"
    if not isinstance(state, dict) or state.get("run_id") != key:
        state = {"run_id": key, "consecutive": 0, "tripped": False}
        try:
            session._guardian_breaker = state
        except Exception:
            pass
    return state


# ---------------------------------------------------------------------------
#  Prompt assembly
# ---------------------------------------------------------------------------


async def _build_prompt(service: Any, session: Any, request: GuardianRequest) -> str:
    transcript = await _compact_transcript(service, session)
    environment = (
        f"kind={request.environment_kind or 'host'}"
        f" network={'yes' if request.environment_has_network else 'no'}"
    )
    action: dict[str, Any] = {
        "kind": request.kind,
        "tool_name": request.tool_name,
        "arguments": _clip(request.arguments, MAX_ACTION_CHARS),
    }
    if request.server_label:
        action["server"] = request.server_label
    if request.metadata:
        action["metadata"] = request.metadata
    return (
        "## Execution environment\n"
        f"{environment}\n\n"
        "## Conversation transcript (most recent last)\n"
        f"{transcript or '(no prior transcript)'}\n\n"
        "## Planned action under review\n"
        f"{json.dumps(action, ensure_ascii=False, default=str)}\n"
    )


def _session_review_lock(session: Any) -> asyncio.Lock:
    lock = getattr(session, "_guardian_review_lock", None)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        try:
            session._guardian_review_lock = lock
        except Exception:
            pass
    return lock
