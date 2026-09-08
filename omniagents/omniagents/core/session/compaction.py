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
import asyncio
import hashlib
import math
from contextvars import ContextVar
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("omniagents.session.compaction")
COMPACTION_TIMEOUT_SECONDS = 45
explicit_native_compaction = ContextVar("explicit_native_compaction", default=False)

# Knobs we consume locally rather than forward to the SDK constructor.
_LOCAL_KNOBS = frozenset(
    {
        "enabled",
        "threshold_fraction",
        "threshold_tokens",
        "threshold_items",
        "interruption_safe",
        "timeout_seconds",
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
        for call in item.get("tool_calls") or []:
            if isinstance(call, dict) and isinstance(call.get("id"), str):
                pending_call_ids.add(call["id"])
        if item.get("role") == "tool" and isinstance(item.get("tool_call_id"), str):
            resolved_call_ids.add(item["tool_call_id"])
    return bool(pending_call_ids - resolved_call_ids)


def has_pending_compaction_tools(session: Any) -> bool:
    """Inspect raw active context before API replay repairs missing outputs."""
    items = getattr(session, "_sdk_history", None)
    if items is None:
        items = getattr(session, "history", [])
    from omniagents.core.runtime.compaction import history_since_last_checkpoint

    return _has_unresolved_function_call(
        history_since_last_checkpoint(items, include_checkpoint=True)
    )


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


def _extract_openai_client(provider: Any) -> Optional[Any]:
    """Pull the ``AsyncOpenAI`` client out of a built SDK ``ModelProvider``.

    Compaction must talk to the *same* endpoint as the parent session —
    including the ``openai-oauth`` (Codex) case, where the client carries a
    per-request token-refresh hook and there is no static ``api_key`` /
    ``base_url`` to rebuild from. The SDK's ``OpenAIProvider`` exposes its
    client via ``_get_client()``; the reasoning-content-replay wrapper
    delegates attribute access to its inner provider, so the same lookup
    works through it. Returns ``None`` for providers with no single OpenAI
    client (LiteLLM / any-llm), where ``responses.compact`` doesn't apply.
    """
    if provider is None:
        return None
    getter = getattr(provider, "_get_client", None)
    if callable(getter):
        try:
            client = getter()
            if client is not None:
                return client
        except Exception:
            pass
    return getattr(provider, "_client", None)


def _build_should_trigger_compaction(
    *,
    model: str,
    threshold_fraction: float,
    threshold_tokens: Optional[int],
    threshold_items: Optional[int],
    interruption_safe: bool,
    context_window_override: Optional[int] = None,
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
    # Prefer the parent model's configured ``max_input_tokens`` (the real
    # serving-model window) over the SDK's static lookup, which won't know
    # operator-named models like ``gpt-5.5``. This is what keeps the trigger
    # honest: compaction fires at a fraction of the window the request will
    # actually be measured against, not a generous guess.
    context_window = context_window_override or _resolve_context_window(model)
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
        # User messages and previous compaction items also occupy the window.
        token_estimate = _candidate_token_estimate(
            context.get("session_items") or items
        )
        return token_estimate >= threshold_tokens

    return should_trigger


_guarded_session_cls_cache: Dict[type, type] = {}

_atomic_session_cls_cache: Dict[type, type] = {}


def _atomic_compaction_session_cls(base: type) -> type:
    """Avoid the SDK's clear/add crash window on our durable context view."""
    if base not in _atomic_session_cls_cache:

        class AtomicCompactionSession(base):
            async def run_compaction(self, args=None):
                session = self.underlying_session
                if has_pending_compaction_tools(session):
                    return
                if (
                    getattr(session, "_checkpoint_compaction_in_progress", False)
                    or getattr(session, "_sdk_compaction_in_progress", False)
                    or (
                        getattr(session, "_compact_command_in_progress", False)
                        and not explicit_native_compaction.get()
                    )
                ):
                    if args and args.get("force"):
                        raise RuntimeError("Context compaction is already in progress.")
                    return
                session._sdk_compaction_in_progress = True
                try:
                    # Cancellation repair and commands append directly to the
                    # underlying session, bypassing the SDK candidate cache.
                    self._compaction_candidate_items = None
                    self._session_items = None
                    return await asyncio.wait_for(
                        super().run_compaction(args),
                        timeout=getattr(
                            self, "_omni_timeout_seconds", COMPACTION_TIMEOUT_SECONDS
                        ),
                    )
                except asyncio.TimeoutError as exc:
                    raise RuntimeError(
                        "Native context compaction timed out; history was preserved."
                    ) from exc
                finally:
                    session._sdk_compaction_in_progress = False

            async def _replace_underlying_session_items(
                self, *, output_items, previous_items
            ):
                if not output_items:
                    raise RuntimeError(
                        "Native compaction returned empty context; history was preserved."
                    )
                replace = getattr(self.underlying_session, "replace_sdk_items", None)
                if callable(replace):
                    await replace(output_items)
                else:
                    await super()._replace_underlying_session_items(
                        output_items=output_items, previous_items=previous_items
                    )

        _atomic_session_cls_cache[base] = AtomicCompactionSession
    return _atomic_session_cls_cache[base]


def _guarded_compaction_session_cls(base: type) -> type:
    """Subclass *base* so ``run_compaction`` itself refuses to compact over
    an unresolved ``function_call``.

    The ``should_trigger_compaction`` hook already declines that state, but
    the SDK's deferred-compaction bookkeeping re-invokes
    ``run_compaction(force=True)`` from interruption finalization
    (``_finalize_streamed_interruption`` → ``save_result_to_session``), and
    ``force`` bypasses the hook entirely — observed in production as a 400
    ("No tool output found for function call ...") when a run was
    interrupted mid-tool-call. Deferring again is always safe: compaction
    re-arms on the next save, once the call/output pair is complete.
    """
    cached = _guarded_session_cls_cache.get(base)
    if cached is not None:
        return cached

    class _InterruptionSafeCompactionSession(base):  # type: ignore[misc, valid-type]
        async def run_compaction(self, args: Any = None) -> None:
            try:
                _, session_items = await self._ensure_compaction_candidates()
            except Exception:
                session_items = None
            if session_items and _has_unresolved_function_call(session_items):
                logger.debug(
                    "Skipping compaction (forced path): pending function_call "
                    "without output would 400 on responses.compact."
                )
                return None
            return await super().run_compaction(args)

    _guarded_session_cls_cache[base] = _InterruptionSafeCompactionSession
    return _InterruptionSafeCompactionSession


def wrap_with_compaction(
    session: Any,
    config: Optional[Dict[str, Any]],
    *,
    model_config: Optional[Dict[str, Any]] = None,
    model_provider: Optional[Any] = None,
    parent_model: Optional[str] = None,
    max_input_tokens: Optional[int] = None,
) -> Any:
    """Wrap *session* with ``OpenAIResponsesCompactionSession`` if enabled.

    *config* mirrors the YAML shape::

        session:
          compaction:
            enabled: true
            compaction_mode: auto
            # model: optional — omit to inherit the parent session's model

    All keys other than the local knobs are forwarded as kwargs to
    :class:`OpenAIResponsesCompactionSession`.

    Compaction is run against the **parent session's own model and provider
    transport** so its ``responses.compact`` calls hit the same endpoint with
    the same credentials. Resolution order:

    * **model** — ``config["model"]`` if pinned, else *parent_model*.
    * **client** — extracted from *model_provider* (the already-built SDK
      ``ModelProvider``), which covers the ``openai-oauth``/Codex case where
      the client carries a per-request token-refresh hook and there is no
      static ``api_key`` to rebuild from. Falls back to building a client
      from *model_config*'s ``api_key`` / ``base_url`` only when the provider
      exposes no reusable client.
    * **trigger window** — *max_input_tokens* (the serving model's real
      context window) when supplied, else the SDK's static lookup.

    Endpoint-compatibility (whether the provider actually serves
    ``responses.compact``) is the operator's responsibility — flip
    ``enabled: false`` per-agent if you point at a backend that doesn't
    implement it.

    Returns the original session unchanged when compaction is disabled or
    when wrapping fails.
    """

    if not config or not isinstance(config, dict):
        return session

    if not config.get("enabled", False):
        return session

    # ``responses.compact`` is a Responses-API-only operation. When the parent
    # session is explicitly pinned to Chat Completions (``use_responses: false``,
    # e.g. Fireworks/other compatible backends that don't implement the
    # Responses API) the wrap would raise "Unsupported model for OpenAI
    # responses compaction" and fall back anyway — skip it quietly instead of
    # emitting a warning on every session boot.
    if model_config and model_config.get("use_responses") is False:
        logger.debug(
            "Skipping Responses compaction: model %r uses Chat Completions "
            "(use_responses=false).",
            model_config.get("model") or parent_model,
        )
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

    # Compaction follows the parent session's model unless the operator
    # pinned one explicitly in YAML. ``responses.compact`` is issued against
    # the parent's own endpoint (see client reuse below), so the model name
    # must match that endpoint — a hardcoded name from a different provider
    # makes every compact call fail (and the failure is swallowed, so the
    # session grows unbounded until the next turn overflows the window).
    if not kwargs.get("model") and parent_model:
        kwargs["model"] = parent_model

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
        # window for the trigger threshold. ``max_input_tokens`` (the parent
        # model's configured window) takes precedence when supplied.
        model_for_window = str(kwargs.get("model") or parent_model or "gpt-4.1")
        kwargs["should_trigger_compaction"] = _build_should_trigger_compaction(
            model=model_for_window,
            threshold_fraction=threshold_fraction,
            threshold_tokens=threshold_tokens,
            threshold_items=threshold_items,
            interruption_safe=interruption_safe,
            context_window_override=max_input_tokens,
        )

    # Preferred path: reuse the parent session's already-built provider
    # client so compaction talks to the exact same endpoint with the same
    # credentials — critically including ``openai-oauth`` (Codex), whose
    # client injects a refreshing bearer token per request and exposes no
    # static ``api_key`` / ``base_url`` to rebuild from.
    if "client" not in kwargs:
        provider_client = _extract_openai_client(model_provider)
        if provider_client is not None:
            kwargs["client"] = provider_client

    # Fallback: rebuild a client from explicit creds in ``model_config``.
    # Only reached when the provider exposed no reusable client (e.g. an
    # openai-compatible model resolved without a built provider object).
    if model_config and "client" not in kwargs:
        api_key = model_config.get("api_key")
        base_url = model_config.get("base_url")
        if api_key:
            try:
                from openai import AsyncOpenAI

                is_azure = bool(base_url) and (
                    "openai.azure.com" in str(base_url)
                    or "services.ai.azure.com" in str(base_url)
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

    # ``interruption_safe`` must guard the ACTUAL compact call, not just the
    # trigger hook — the SDK's deferred path invokes run_compaction with
    # force=True, which skips the hook (see _guarded_compaction_session_cls).
    session_cls: Any = OpenAIResponsesCompactionSession
    if bool(config.get("interruption_safe", True)):
        session_cls = _guarded_compaction_session_cls(OpenAIResponsesCompactionSession)
    session_cls = _atomic_compaction_session_cls(session_cls)

    try:
        wrapped = session_cls(
            session_id=session_id,
            underlying_session=session,
            **kwargs,
        )
        timeout = float(config.get("timeout_seconds", COMPACTION_TIMEOUT_SECONDS))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Compaction timeout_seconds must be finite and positive")
        wrapped._omni_timeout_seconds = timeout
        return wrapped
    except Exception as exc:
        logger.warning(
            "Failed to wrap session with OpenAIResponsesCompactionSession: %s",
            exc,
        )
        return session


def native_compaction_for_session(
    service: Any, session: Any, *, model_config=None, model_provider=None
) -> Any:
    """Resolve native compaction for commands, including freshly loaded sessions.

    A native setup failure is an error, never permission to silently switch
    an opted-in Responses session to agent summarization.
    """
    cached = getattr(session, "_sdk_compaction_session", None)
    config = getattr(getattr(service, "spec", None), "session_compaction_config", None)
    if config is None:
        return cached
    if not config or not config.get("enabled", False):
        session._sdk_compaction_session = None
        return None
    from omniagents.core.models import (
        resolve_service_model_runtime,
        provider_from_runtime,
    )

    if model_config is None:
        _, runtime = resolve_service_model_runtime(service, session)
    else:
        runtime = model_config
    if runtime.get("use_responses") is False:
        session._sdk_compaction_session = None
        return None
    signature = hashlib.sha256(
        json.dumps(
            [
                config,
                runtime,
                getattr(getattr(service, "spec", None), "model_provider_config", None),
            ],
            sort_keys=True,
            default=repr,
        ).encode()
    ).digest()
    if (
        cached is not None
        and getattr(session, "_sdk_compaction_signature", None) == signature
    ):
        return cached
    # Never keep using a former model/provider after a failed reconfiguration.
    session._sdk_compaction_session = None
    provider = model_provider
    if provider is None:
        provider, _ = provider_from_runtime(runtime)
    wrapped = wrap_with_compaction(
        session,
        config,
        model_config=runtime,
        model_provider=provider,
        parent_model=runtime.get("model"),
        max_input_tokens=runtime.get("max_input_tokens")
        or (getattr(session, "model_config", None) or {}).get("max_input_tokens"),
    )
    if wrapped is session:
        raise RuntimeError("Native context compaction could not be initialized.")
    session._sdk_compaction_session = wrapped
    session._sdk_compaction_signature = signature
    return wrapped
