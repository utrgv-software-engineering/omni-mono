"""The model an evaluation's agents and judges run on, resolved as a real run resolves it.

A production run gets its model provider and the catalog's per-model
settings from the model store (the project's ``paths.model_catalog`` plus
the user's ``models.json``): for a ChatGPT-subscription catalog that is the
Codex OAuth provider with ``store: false`` and encrypted reasoning. An
evaluation that ran agents on the SDK's default provider instead needed an
``OPENAI_API_KEY`` and could not use a subscription at all.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

__all__ = [
    "EvalModel",
    "merged_model_settings",
    "resolve_eval_model",
    "run_to_final_output",
    "with_provider",
]


@dataclass(frozen=True)
class EvalModel:
    """A model name with the provider and catalog settings a run of it needs."""

    name: str
    provider: Any = None
    model_settings: Dict[str, Any] = field(default_factory=dict)
    reasoning: Optional[str] = None


def resolve_eval_model(model_name: Optional[str]) -> Optional[EvalModel]:
    """Resolve ``model_name`` (or the store's default) through the model store.

    Returns ``None`` when there is no store or it doesn't know the model, so
    the run uses the SDK default as before. A single-provider store whose
    provider can't be built raises, as production does: no other provider is
    used behind the user's back.
    """
    from omniagents.core.models import model_store
    from omniagents.core.models.runtime import provider_from_runtime

    store = model_store()
    if store is None:
        return None
    name = model_name or _default_model(store)
    if not name:
        return None
    runtime = store.resolve_model_for_runtime(name)
    if not runtime:
        return None
    provider, _env = provider_from_runtime(runtime)
    if provider is None and getattr(store, "single_provider", False):
        raise ValueError(
            f"The workspace provider for {name!r} is unavailable (signed out?); "
            "no other provider will be used."
        )
    return EvalModel(
        name=str(runtime.get("model") or name),
        provider=provider,
        model_settings=dict(runtime.get("model_settings") or {}),
        reasoning=runtime.get("reasoning"),
    )


def _default_model(store: Any) -> Optional[str]:
    for attr in ("get_default_model_name", "default_model_name"):
        getter = getattr(store, attr, None)
        try:
            value = getter() if callable(getter) else getter
        except Exception:
            value = None
        if isinstance(value, str) and value.strip():
            return value
    return None


def merged_model_settings(
    own: Optional[Dict[str, Any]], model: Optional[EvalModel]
) -> Optional[Dict[str, Any]]:
    """The catalog's settings for the model, with the agent's own on top."""
    if model is None:
        return own
    merged: Dict[str, Any] = {**model.model_settings, **(own or {})}
    if "reasoning" not in merged and model.reasoning:
        merged["reasoning"] = {"effort": model.reasoning}
    return merged or None


def with_provider(run_config: Any, provider: Any) -> Any:
    """``run_config`` carrying ``provider`` (a new RunConfig when there is none)."""
    if provider is None:
        return run_config
    from agents import RunConfig

    if run_config is None:
        return RunConfig(model_provider=provider)
    return dataclasses.replace(run_config, model_provider=provider)


async def run_to_final_output(
    agent: Any,
    prompt: str,
    *,
    provider: Any = None,
    context: Optional[Dict[str, Any]] = None,
    max_turns: int = 1,
) -> Any:
    """Run ``agent`` once, streamed, and return its final output.

    Streamed because some backends (the Codex backend behind a ChatGPT
    subscription) accept only streaming requests; for every other model the
    streaming runner takes the same per-turn path.
    """
    from agents import Runner

    result = Runner.run_streamed(
        agent,
        prompt,
        context=context,
        max_turns=max_turns,
        run_config=with_provider(None, provider),
    )
    try:
        async for _ in result.stream_events():
            pass
    except BaseException:
        result.cancel()
        raise
    return result.final_output
