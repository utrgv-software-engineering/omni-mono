"""Ephemeral agent runs: run a project agent once, outside the session
lifecycle.

Utility agents (context compactors, recappers, session summarizers)
deliberately bypass ``AgentService`` — their runs must not append to the
user's session history, emit UI events, or trigger notifications. That
is a legitimate use of the SDK ``Runner``; what products kept
re-implementing around it was the same boilerplate: load the spec,
override the model, build the agent, wire a model provider, stamp a
workspace_root, drain the stream, extract the text.
:func:`run_agent_once` owns that boilerplate.

What this deliberately does NOT do: sessions, persistence, approvals
(there is no channel to answer them — keep utility agents tool-less or
approval-free), notifications, tracing beyond what the SDK does on its
own. If you need those, run through ``AgentService``.

:func:`run_project_agent_once` is the project-aware layer on top:
resolve ``agents/<agent_key>/agent.yml`` through a project manifest
(defaulting to the installed product's — ``ProductInfo.manifest_path``),
apply the spec-level overrides products kept re-implementing
(``model_settings`` stamp, ``instructions`` swap), and delegate.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Optional


@contextmanager
def temporary_environ(overrides: Dict[str, Optional[str]]):
    """Apply environment overrides for the duration of the block.

    ``None`` values unset the variable. Prior values (or absence) are
    restored on exit.
    """
    prior: Dict[str, Optional[str]] = {}
    for key, value in (overrides or {}).items():
        prior[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, value in prior.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def run_agent_once(
    agent: Any,
    prompt: str,
    *,
    model: Optional[str] = None,
    model_provider: Any = None,
    env_overrides: Optional[Dict[str, Optional[str]]] = None,
    session: Any = None,
    workspace_root: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    max_turns: int = 1,
) -> str:
    """Run *agent* once with *prompt* and return its final text output.

    Args:
        agent: A path to an ``agent.yml`` (str or Path) or a pre-loaded
            ``AgentSpec``.
        prompt: The single user input for the run.
        model: Optional model-name override applied to the spec.
        model_provider: Optional SDK ``ModelProvider`` for the run (e.g.
            a provider carrying OAuth credentials). Products resolve this
            themselves — provider wiring is deployment policy.
        env_overrides: Environment variables to apply for the duration of
            the run (e.g. per-provider API keys). ``None`` values unset.
        session: Optional live Session; used only as a workspace_root
            fallback source and passed to the agent builder (some
            builders read session-scoped settings). The run does NOT
            append to the session.
        workspace_root: Explicit workspace root for the run context.
            Falls back to ``session.context["workspace_root"]``, then the
            process cwd. Runs bypass the AgentService resolver, so pass
            an explicit value when host cwd is not the right anchor
            (e.g. sandboxed deployments).
        context: Extra run-context entries merged over the workspace_root
            stamp.
        max_turns: Turn budget; utility agents default to a single turn.

    Returns:
        The run's final output as a string.
    """
    from agents import RunConfig, Runner

    from omniagents.core.agents.builder import _default_build_agent
    from omniagents.core.config.loader import load_agent_spec_from_yaml

    if isinstance(agent, (str, Path)):
        spec = load_agent_spec_from_yaml(str(agent))
    else:
        spec = agent
    if model:
        spec.model_name = model

    built = await _default_build_agent(
        settings={}, mcp_servers=None, spec=spec, session=session
    )

    run_config = (
        RunConfig(model_provider=model_provider) if model_provider is not None else None
    )

    if not workspace_root and session is not None:
        sess_ctx = getattr(session, "context", None)
        if isinstance(sess_ctx, dict):
            wr = sess_ctx.get("workspace_root")
            if isinstance(wr, str) and wr.strip():
                workspace_root = wr
    if not workspace_root:
        workspace_root = os.getcwd()

    run_context: Dict[str, Any] = {"workspace_root": workspace_root}
    if context:
        run_context.update(context)

    with temporary_environ(env_overrides or {}):
        # Use the streaming runner: some backends (e.g. Codex) require
        # stream=true, and for every other model run_streamed routes
        # through the same per-turn path via the disable_token_streaming
        # patch.
        result = Runner.run_streamed(
            built,
            prompt,
            context=run_context,
            max_turns=max_turns,
            run_config=run_config,
        )
        async for _ in result.stream_events():
            pass
        return result.final_output_as(str)


def resolve_project_agent_yaml(
    agent_key: str, project_path: Any = None
) -> Path:
    """Locate ``<agents-dir>/<agent_key>/agent.yml`` via a project manifest.

    *project_path* is a project.yml path or a directory containing one;
    when omitted, the installed product identity's manifest is used
    (``product_info().manifest_path``, stamped by ``bootstrap_product`` /
    ``load_product_info`` / ``load_project``). This is a light manifest
    read — only ``paths.agents`` (default ``agents``) is consulted, no
    agent discovery, no overlays — mirroring ``load_product_info``.
    """
    import yaml

    if project_path is None:
        from omniagents.core.product import product_info

        project_path = product_info().manifest_path
    if project_path is None:
        raise ValueError(
            "No project manifest to resolve agents against: pass "
            "project_path=, or install a product identity first "
            "(bootstrap_product / load_project)."
        )
    manifest = Path(project_path).expanduser()
    if manifest.is_dir():
        manifest = manifest / "project.yml"
    if not manifest.is_file():
        raise FileNotFoundError(f"Could not find project manifest: {manifest}")
    base_dir = manifest.parent

    try:
        proj = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    except Exception:
        proj = {}
    paths_cfg = proj.get("paths") if isinstance(proj, dict) else None
    agents_cfg: Any = "agents"
    if isinstance(paths_cfg, dict):
        agents_cfg = paths_cfg.get("agents", "agents")
    if isinstance(agents_cfg, str):
        agents_entries = [agents_cfg]
    elif isinstance(agents_cfg, list):
        agents_entries = [str(x) for x in agents_cfg]
    else:
        agents_entries = ["agents"]

    tried: list[Path] = []
    for entry in agents_entries:
        d = Path(entry)
        if not d.is_absolute():
            d = (base_dir / entry).resolve()
        candidate = d / agent_key / "agent.yml"
        if candidate.is_file():
            return candidate
        tried.append(candidate)
    raise FileNotFoundError(
        f"No agent {agent_key!r} in project {manifest}: tried "
        + ", ".join(str(t) for t in tried)
    )


async def run_project_agent_once(
    agent_key: str,
    prompt: str,
    *,
    project_path: Any = None,
    model: Optional[str] = None,
    runtime: Optional[Dict[str, Any]] = None,
    model_provider: Any = None,
    env_overrides: Optional[Dict[str, Optional[str]]] = None,
    session: Any = None,
    workspace_root: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    model_settings: Optional[Dict[str, Any]] = None,
    max_turns: int = 1,
    instructions: Optional[str] = None,
) -> str:
    """Run a *project* agent once, resolved by key through its manifest.

    The project-aware front of :func:`run_agent_once`: products name the
    agent (``agents/<agent_key>/agent.yml`` under the manifest's
    ``paths.agents``) instead of hand-building the path, and get the two
    spec-level overrides every utility runner re-implemented:

    Args (beyond :func:`run_agent_once`'s, which pass straight through):
        agent_key: Directory key of the agent under the project's agents
            dir(s).
        project_path: Manifest (project.yml or its directory) to resolve
            against; defaults to the installed product's manifest
            (``product_info().manifest_path``).
        runtime: Optional resolved model-runtime dict. When given and no
            explicit ``model_provider``/``env_overrides`` are passed, it
            is turned into a provider via
            :func:`omniagents.core.models.provider_from_runtime`.
            Products with provider policy of their own (e.g. binding a
            token store before the builder runs) should resolve the
            provider themselves and pass ``model_provider`` /
            ``env_overrides`` instead.
        model_settings: When a non-empty dict, stamped onto the loaded
            spec (the one spec field :func:`run_agent_once` doesn't
            touch).
        instructions: When given, replaces the spec's system prompt for
            this run — for prompts whose single source of truth lives in
            code rather than the agent dir's ``instructions.md``.

    Returns:
        The run's final output as a string.
    """
    from omniagents.core.config.loader import load_agent_spec_from_yaml

    agent_yaml = resolve_project_agent_yaml(agent_key, project_path)
    agent: Any = agent_yaml
    wants_settings = isinstance(model_settings, dict) and bool(model_settings)
    if wants_settings or instructions is not None:
        # run_agent_once applies the model-name override itself; stamp
        # the remaining spec-level overrides on a loaded spec and hand
        # the spec (not the path) to the harness.
        spec = load_agent_spec_from_yaml(str(agent_yaml))
        if wants_settings:
            spec.model_settings = model_settings
        if instructions is not None:
            spec.get_agent_instructions = lambda: instructions
        agent = spec

    if model_provider is None and env_overrides is None and runtime is not None:
        from omniagents.core.models import provider_from_runtime

        model_provider, env_overrides = provider_from_runtime(runtime)

    return await run_agent_once(
        agent,
        prompt,
        model=model,
        model_provider=model_provider,
        env_overrides=env_overrides,
        session=session,
        workspace_root=workspace_root,
        context=context,
        max_turns=max_turns,
    )
