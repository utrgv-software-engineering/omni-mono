"""SDK-native compaction wrapping for OmniAgents sessions.

This thin helper wraps an OmniAgents :class:`~omniagents.core.session.manager.Session`
with the SDK's :class:`~agents.memory.OpenAIResponsesCompactionSession` so the
OpenAI Responses ``compact`` endpoint manages context-window pressure instead
of the legacy custom heuristics in :mod:`omniagents.core.runtime.compaction`.

When compaction is disabled (``config`` empty/falsy or ``enabled`` flag false),
the original session is returned unchanged so existing call sites stay
backwards-compatible.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("omniagents.session.compaction")

# Knobs we consume locally rather than forward to the SDK constructor.
_LOCAL_KNOBS = frozenset(
    {
        "enabled",
        "threshold_fraction",
        "threshold_tokens",
        "threshold_items",
        "interruption_safe",
    }
)

# Fallback when the SDK doesn't know the model's context window. Picked
# generously: most modern models are >=128k, and SDK count-of-10 is far
# too aggressive — we'd rather skip-and-let-reactive-compaction-handle-it
# than compact every other turn.
_FALLBACK_THRESHOLD_TOKENS = 100_000


def _candidate_token_estimate(items: List[Any]) -> int:
    """Rough token estimate for ``compaction_candidate_items``.

    Compaction triggers should respond to context pressure, not item count
    — but we don't want to take a hard ``tiktoken`` dep just for an
    approximation. Sum the JSON-serialized character length of each
    candidate and divide by 4, which tracks BPE token counts within ~20%
    for English text and is well within "is this turn big" territory.
    """
    total_chars = 0
    for item in items:
        try:
            if isinstance(item, str):
                total_chars += len(item)
            else:
                total_chars += len(json.dumps(item, default=str))
        except Exception:
            # Defensive: never let an estimate failure block compaction
            # entirely — fall back to a per-item nominal cost.
            total_chars += 200
    return total_chars // 4


def _has_unresolved_function_call(items: List[Any]) -> bool:
    """Return True when any ``function_call`` lacks a matching ``function_call_output``.

    This is the failure mode the Responses API rejects with HTTP 400
    ("No tool output found for function call ...") during
    ``responses.compact``. Skipping compaction when this is true keeps
    tool-approval interruptions from blowing up mid-run.
    """
    pending_call_ids: set[str] = set()
    resolved_call_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        call_id = item.get("call_id") or item.get("id")
        if item_type == "function_call" and isinstance(call_id, str):
            pending_call_ids.add(call_id)
        elif item_type == "function_call_output" and isinstance(call_id, str):
            resolved_call_ids.add(call_id)
    return bool(pending_call_ids - resolved_call_ids)


def _resolve_context_window(model: str) -> Optional[int]:
    """Look up the model's context window via the SDK's table.

    The Compaction *capability* (sandbox) carries ``CompactionModelInfo``
    with a ``_MODEL_CONTEXT_WINDOWS`` map. We re-use it here so the
    session-wrapped Agents path stays consistent with the capability
    path's ``DynamicCompactionPolicy(threshold=0.9 * context_window)``
    behavior — even though we're not wiring a SandboxAgent.
    """
    try:
        from agents.sandbox.capabilities.compaction import (
            CompactionModelInfo,
        )
    except Exception:
        return None
    info = CompactionModelInfo.maybe_for_model(model)
    return info.context_window if info is not None else None


def _build_should_trigger_compaction(
    *,
    model: str,
    threshold_fraction: float,
    threshold_tokens: Optional[int],
    threshold_items: Optional[int],
    interruption_safe: bool,
) -> Callable[[Dict[str, Any]], bool]:
    """Build the predicate passed to ``OpenAIResponsesCompactionSession``.

    Default: trigger when candidate items are estimated to exceed
    ``threshold_fraction`` (default 0.9) of the model's context window
    — mirroring the SDK's ``DynamicCompactionPolicy``. Falls back to a
    flat 100k-token cap when the model isn't in the SDK's window map,
    or to ``threshold_items`` if the operator opted into count-based
    behavior in YAML.

    Always returns False when ``interruption_safe`` is True and a
    ``function_call`` in the candidates has no matching output — the
    Responses ``compact`` API rejects that state with HTTP 400 and the
    resulting non-retryable error otherwise tanks the run.
    """
    context_window = _resolve_context_window(model)
    if threshold_tokens is None:
        if context_window is not None:
            threshold_tokens = int(context_window * threshold_fraction)
        else:
            logger.info(
                "Compaction model %r not in SDK context-window map; "
                "falling back to %d-token threshold.",
                model,
                _FALLBACK_THRESHOLD_TOKENS,
            )
            threshold_tokens = _FALLBACK_THRESHOLD_TOKENS

    def should_trigger(context: Dict[str, Any]) -> bool:
        items = context.get("compaction_candidate_items") or []
        if interruption_safe and _has_unresolved_function_call(items):
            logger.debug(
                "Skipping compaction: pending function_call without output "
                "(would 400 on responses.compact)."
            )
            return False
        if threshold_items is not None and len(items) >= threshold_items:
            return True
        token_estimate = _candidate_token_estimate(items)
        return token_estimate >= threshold_tokens

    return should_trigger


def wrap_with_compaction(
    session: Any,
    config: Optional[Dict[str, Any]],
    *,
    model_config: Optional[Dict[str, Any]] = None,
) -> Any:
    """Wrap *session* with ``OpenAIResponsesCompactionSession`` if enabled.

    *config* mirrors the YAML shape::

        session:
          compaction:
            enabled: true
            model: gpt-4.1
            compaction_mode: auto

    All keys other than ``enabled`` are forwarded as kwargs to
    :class:`OpenAIResponsesCompactionSession`.

    *model_config* is the resolved per-model transport (provider, base_url,
    api_key) from the agent's model resolver. When supplied with an
    ``api_key``, a dedicated ``AsyncOpenAI`` client is built and passed as
    ``client=`` so the wrap does not depend on ambient ``OPENAI_API_KEY`` /
    ``OPENAI_BASE_URL`` env vars. Endpoint-compatibility (whether the
    provider actually serves ``responses.compact``) is the operator's
    responsibility — flip ``enabled: false`` per-agent if you point at a
    proxy that doesn't implement it.

    Returns the original session unchanged when compaction is disabled or
    when wrapping fails.
    """

    if not config or not isinstance(config, dict):
        return session
    if not config.get("enabled", False):
        return session

    try:
        from agents.memory import OpenAIResponsesCompactionSession
    except Exception as exc:  # pragma: no cover - SDK should always be present
        logger.warning(
            "Failed to import OpenAIResponsesCompactionSession; compaction disabled: %s",
            exc,
        )
        return session

    kwargs: Dict[str, Any] = {k: v for k, v in config.items() if k not in _LOCAL_KNOBS}

    # Build a sensible ``should_trigger_compaction`` predicate unless the
    # operator passed one explicitly. The SDK default fires at 10 non-user
    # items, which is so aggressive it triggers mid-tool-approval and the
    # ``responses.compact`` API rejects the pending function_call with 400.
    # We replace it with a token-aware threshold + an interruption guard.
    if "should_trigger_compaction" not in kwargs:
        try:
            threshold_fraction = float(config.get("threshold_fraction", 0.9))
        except (TypeError, ValueError):
            threshold_fraction = 0.9
        raw_tokens = config.get("threshold_tokens")
        threshold_tokens: Optional[int] = None
        if raw_tokens is not None:
            try:
                threshold_tokens = int(raw_tokens)
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid threshold_tokens %r in session.compaction; ignoring.",
                    raw_tokens,
                )
        raw_items = config.get("threshold_items")
        threshold_items: Optional[int] = None
        if raw_items is not None:
            try:
                threshold_items = int(raw_items)
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid threshold_items %r in session.compaction; ignoring.",
                    raw_items,
                )
        interruption_safe = bool(config.get("interruption_safe", True))
        # The session-wrap model knob is what the SDK uses for the
        # responses.compact call itself; reuse it to look up the context
        # window for the trigger threshold.
        model_for_window = str(kwargs.get("model", "gpt-4.1"))
        kwargs["should_trigger_compaction"] = _build_should_trigger_compaction(
            model=model_for_window,
            threshold_fraction=threshold_fraction,
            threshold_tokens=threshold_tokens,
            threshold_items=threshold_items,
            interruption_safe=interruption_safe,
        )

    if model_config and "client" not in kwargs:
        api_key = model_config.get("api_key")
        base_url = model_config.get("base_url")
        if api_key:
            try:
                from openai import AsyncOpenAI

                is_azure = bool(base_url) and (
                    "openai.azure.com" in base_url
                    or "services.ai.azure.com" in base_url
                )
                if is_azure:
                    # Azure rejects ``Authorization: Bearer`` and wants the
                    # key in the ``api-key`` header instead. Mirror the
                    # treatment used by builder.py and omni-code's reactive
                    # compaction client.
                    kwargs["client"] = AsyncOpenAI(
                        api_key="dummy",
                        base_url=base_url,
                        default_headers={"api-key": api_key},
                    )
                else:
                    client_kwargs: Dict[str, Any] = {"api_key": api_key}
                    if base_url:
                        client_kwargs["base_url"] = base_url
                    kwargs["client"] = AsyncOpenAI(**client_kwargs)
            except Exception as exc:
                logger.warning(
                    "Failed to build AsyncOpenAI client for compaction; "
                    "falling back to default client: %s",
                    exc,
                )

    session_id = getattr(session, "session_id", None) or getattr(session, "id", None)
    if not session_id:
        logger.warning("Session has no session_id/id; compaction wrapping skipped.")
        return session

    try:
        return OpenAIResponsesCompactionSession(
            session_id=session_id,
            underlying_session=session,
            **kwargs,
        )
    except Exception as exc:
        logger.warning(
            "Failed to wrap session with OpenAIResponsesCompactionSession: %s",
            exc,
        )
        return session
