"""Project-backed worker spec loading.

Every product wiring workers to a project registry repeats the same
recipe: build the worker's AgentSpec through ``build_spec_from_project``
(so the worker inherits the project's full capability/tool/guardrails/
server-function registries — loading the worker YAML directly would pass
``capability_registry=None`` and silently strip every capability), then
stamp the product-resolved model onto the spec. Execution placement belongs
to the worker Run and is never copied through AgentSpec.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Tuple


def make_project_worker_spec_loader(
    *,
    project_provider: Callable[[], Any],
    agent_key: str = "worker",
    project_slug: Optional[str] = None,
    model_resolver: Optional[Callable[[Optional[str]], Any]] = None,
) -> Callable[[Optional[str]], Tuple[Any, Any]]:
    """Build a ``WorkerDefaults.spec_loader`` bound to a project registry.

    The returned ``loader(model) -> (spec, model_runtime)`` matches the
    callable shape ``WorkerManager`` expects.

    Args:
        project_provider: Returns the ``ProjectContext`` to build the
            worker spec against (typically the running server's published
            context, with a cached ``load_project`` fallback).
        agent_key: The project agent key of the worker agent.
        project_slug: When set, pinned onto ``spec.project_slug`` so
            worker sessions/traces stay grouped even if the project name
            drifts.
        model_resolver: ``resolver(model) -> model_runtime`` — the
            product's model resolution (``model`` is the optional
            caller-requested model, None for the product default). When
            the result is a dict, its ``model`` / ``model_settings``
            entries are stamped onto the spec and the whole dict is
            exposed via ``spec.resolve_default_model_config``.
    """

    def load_worker_spec(model: Optional[str] = None) -> Tuple[Any, Any]:
        from omniagents.core.project.runtime import build_spec_from_project

        model_runtime = model_resolver(model) if model_resolver is not None else None

        spec = build_spec_from_project(project_provider(), agent_key=agent_key)
        if project_slug:
            spec.project_slug = project_slug

        resolved_model = (
            model_runtime.get("model") if isinstance(model_runtime, dict) else None
        )
        if isinstance(resolved_model, str) and resolved_model.strip():
            spec.model_name = resolved_model

        resolved_model_settings = (
            model_runtime.get("model_settings")
            if isinstance(model_runtime, dict)
            else None
        )
        if isinstance(resolved_model_settings, dict) and resolved_model_settings:
            spec.model_settings = resolved_model_settings

        spec.resolve_default_model_config = lambda: (
            model_runtime if isinstance(model_runtime, dict) else None
        )

        return spec, model_runtime

    return load_worker_spec
