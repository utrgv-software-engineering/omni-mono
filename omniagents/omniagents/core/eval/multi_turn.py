"""Evaluation pipeline with environment and scenario type support.

This module extends the standard evaluation pipeline to support:
- Scripted scenarios: single-turn (prompt) or multi-turn (prompts)
- Synthetic scenarios: LLM-driven simulated user conversations
- Environment setup/teardown for fixtures
- Database/API verification

Scenario Types:
- Scripted (default): User messages are predefined
  - Single-turn: `prompt: "..."` (string)
  - Multi-turn: `prompts: ["...", "..."]` (list)
- Synthetic: `type: synthetic` with LLM-generated user responses
  - Requires `goal: "..."` (what the simulated user wants)
  - Optional `persona: {name, email, behavior, ...}`
  - Optional `max_exchanges: N` (default 10)
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

import yaml
from agents import RunConfig

from omniagents.core.config.loader import load_agent_spec_from_yaml
from omniagents.core.agents.factory import create_unified_agent
from omniagents.core.session.history_db import load_history
from omniagents.core.evaluation import (
    build_eval_context,
    run_measures,
    final_assistant_text,
)

from .environment import EvalEnvironment, EnvironmentContext, NoOpEnvironment
from .environment_config import build_environment
from .simulated_user import SimulatedUser, UserPersona
from .telemetry import consume_stream_events, scenario_timer, build_telemetry

__all__ = [
    "run_scenario",
    "run_scenarios_against_agent",
    "run_evaluation_with_environment_async",
]

_logger = logging.getLogger(__name__)


def _build_registries_for_eval(
    compiled_yaml_path: Path,
    project_root: Path | None = None,
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Build tool, server function, and capability registries for evaluation.

    Discovers tools from builtins, the project-level directories, and the
    agent's own subdirectories so that ``load_agent_spec_from_yaml`` can
    resolve every tool name and server function.

    The capability registry is sourced from the production
    ``load_project`` loader so the eval-side agent gets the *same* capability
    surface as a real run — including ``shell`` / ``filesystem`` / ``workers``,
    which carry the agent's ``execute_bash`` and file tools. Without it the
    loader skips every ``capabilities:`` entry ("no capability_registry
    available") and the agent runs toolless.

    Returns:
        Tuple of (tool_registry, server_functions_registry, capability_registry).
    """
    from omniagents.core.tools.discovery import discover_functions_in_dir
    from omniagents.core.server_functions.discovery import (
        discover_server_functions_in_dir,
    )

    tool_registry: Dict[str, Any] = {}
    sf_registry: Dict[str, Any] = {}

    # 1. Builtin tools (shipped with omniagents)
    core_root = Path(__file__).resolve().parents[2]
    builtin_dir = (core_root / "builtin" / "tools").resolve()
    if builtin_dir.is_dir():
        try:
            tool_registry.update(discover_functions_in_dir(str(builtin_dir)))
        except Exception:
            pass

    # 2. Resolve project root
    cfg_path = Path(compiled_yaml_path).resolve()
    if project_root is None:
        project_root = cfg_path.parent
        # Legacy fallback: compiled YAML in target/ subdir
        if project_root.name == "target":
            project_root = project_root.parent

    # Read project.yml to find configured tool and server_functions directories
    for candidate in ("project.yml", "project.yaml"):
        project_yml = project_root / candidate
        if project_yml.is_file():
            try:
                proj_cfg = yaml.safe_load(project_yml.read_text(encoding="utf-8")) or {}
                paths = proj_cfg.get("paths", {})
                tools_rel = paths.get("tools")
                if tools_rel:
                    tools_dir = (project_root / tools_rel).resolve()
                    if tools_dir.is_dir():
                        tool_registry.update(discover_functions_in_dir(str(tools_dir)))
                sf_rel = paths.get("server_functions")
                if sf_rel:
                    sf_dir = (project_root / sf_rel).resolve()
                    if sf_dir.is_dir():
                        sf_registry.update(
                            discover_server_functions_in_dir(str(sf_dir))
                        )
            except Exception:
                pass
            break

    # 3. Agent-level tools dir
    agent_tools_dir = cfg_path.parent / "tools"
    if agent_tools_dir.is_dir():
        try:
            tool_registry.update(discover_functions_in_dir(str(agent_tools_dir)))
        except Exception:
            pass

    # 4. Agent-level server_functions dir
    agent_sf_dir = cfg_path.parent / "server_functions"
    if agent_sf_dir.is_dir():
        try:
            sf_registry.update(discover_server_functions_in_dir(str(agent_sf_dir)))
        except Exception:
            pass

    # 5. Capability registry — reuse the production project loader so the
    # eval agent sees the same capabilities (shell/filesystem/workers/...) as
    # a real run. These carry execute_bash and the file tools; omitting them
    # leaves the agent unable to act. Best-effort, matching the rest of this
    # builder: an unloadable project yields an empty capability registry.
    capability_registry: Dict[str, Any] = {}
    try:
        from omniagents.core.project.runtime import load_project

        for candidate in ("project.yml", "project.yaml"):
            project_yml = project_root / candidate
            if project_yml.is_file():
                ctx = load_project(str(project_yml))
                capability_registry = dict(
                    getattr(ctx, "capability_registry", {}) or {}
                )
                break
    except Exception:
        capability_registry = {}

    return tool_registry, sf_registry, capability_registry


def _load_user_mcp_for_eval(
    compiled_yaml_path: Path,
    project_root: Path | None = None,
) -> List[Any]:
    """Resolve user MCP servers for an eval run.

    Mirrors what ``omniagents.core.project.runtime.load_project`` does for
    production runs: when ``project.yml`` declares ``allow_user_mcp_servers:
    true``, load mcp.json from the user-global config dir and the
    project-local one. The eval pipeline calls ``load_agent_spec_from_yaml``
    directly and would otherwise skip this entirely.

    Returns an empty list when the project doesn't opt in or when no
    mcp.json files are found.
    """
    cfg_path = Path(compiled_yaml_path).resolve()
    if project_root is None:
        project_root = cfg_path.parent
        if project_root.name == "target":
            project_root = project_root.parent

    proj_cfg: Dict[str, Any] = {}
    for candidate in ("project.yml", "project.yaml"):
        project_yml = project_root / candidate
        if project_yml.is_file():
            try:
                proj_cfg = yaml.safe_load(project_yml.read_text(encoding="utf-8")) or {}
            except Exception:
                proj_cfg = {}
            break

    if not proj_cfg.get("allow_user_mcp_servers"):
        return []

    try:
        from omniagents.core.config.user_mcp import load_user_mcp_config

        project_name = proj_cfg.get("name") or project_root.name
        return list(
            load_user_mcp_config(
                project_root=project_root,
                project_name=str(project_name),
            )
        )
    except Exception as exc:
        _logger.warning("user MCP config load failed: %s", exc)
        return []


def _get_scenario_type(scenario: Dict[str, Any]) -> str:
    """Determine scenario type from configuration.

    Returns:
        'scripted' (default) or 'synthetic'
    """
    return scenario.get("type", "scripted")


def _apply_scenario_variables(spec, scenario: Dict[str, Any]) -> None:
    """Merge ``scenario.variables`` into ``spec.variables``; scenario wins.

    Mirrors the launcher's ``start_run(variables=...)`` pattern: scenario
    YAML can set per-scenario session variables (e.g.
    ``additional_instructions``) that flow through ``AgentSpec.variables``
    → ``RemoteAgent._variables`` → ``start_run`` → ``session.variables``,
    where the agent's ``build_context`` factory picks them up and renders
    them into the instructions template.

    Without this, scenarios cannot inject system-level guidance the way
    real callers (launcher autopilot, custom dispatchers) do — only the
    agent YAML's baseline ``variables:`` block flows through, and there
    is no per-scenario override.

    No-op if the scenario has no ``variables`` field or it is not a dict.
    """
    scenario_vars = scenario.get("variables")
    if not isinstance(scenario_vars, dict) or not scenario_vars:
        return
    if spec.variables is None:
        spec.variables = dict(scenario_vars)
    else:
        spec.variables = {**spec.variables, **scenario_vars}


def _is_scripted_multi_turn(scenario: Dict[str, Any]) -> bool:
    """Check if scenario is scripted multi-turn.

    True when the scenario opts into any multi-turn behaviour:
      - ``prompts: [...]``      — a fixed list of user turns
      - ``max_continuations``   — loop a single prompt up to N turns
      - ``continuation_prompt`` — a different prompt for repeats
      - ``until_sentinel``      — early-stop on a substring in the agent's
                                  last assistant message
      - ``until_sql``           — early-stop when a SELECT against the
                                  environment-staged DB returns a row
    """
    if "prompts" in scenario and isinstance(scenario["prompts"], list):
        return True
    if scenario.get("max_continuations"):
        return True
    if scenario.get("until_sentinel"):
        return True
    if scenario.get("until_sql"):
        return True
    if scenario.get("continuation_prompt"):
        return True
    return False


async def run_scenario(
    agent_yaml: str,
    scenario: Dict[str, Any],
    environment: Optional[EvalEnvironment] = None,
    defaults: Optional[Dict[str, Any]] = None,
    eval_cfg: Optional[Dict[str, Any]] = None,
    workflow_name: Optional[str] = None,
    studio_trace: Optional[Any] = None,
    project_root: Optional[Path] = None,
    eval_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run a single scenario with environment support.

    Handles both scripted and synthetic scenarios:
    - Scripted single-turn: `prompt: "..."`
    - Scripted multi-turn: `prompts: ["...", "..."]`
    - Synthetic: `type: synthetic` with `goal: "..."`

    Args:
        agent_yaml: Path to the agent YAML configuration.
        scenario: The scenario configuration.
        environment: Optional EvalEnvironment for fixture management.
        defaults: Default settings from evaluation config.
        eval_cfg: Full evaluation configuration.
        workflow_name: Optional workflow name for tracing.
        studio_trace: Optional studio trace context manager.
        project_root: Optional project root for tool discovery.

    Returns:
        Dict with scenario results including measures, history, etc.
    """
    defaults = defaults or {}
    eval_cfg = eval_cfg or {}
    env = environment or NoOpEnvironment()
    agent = None
    saved_env_vars: Dict[str, Optional[str]] = {}  # Track original env var values

    scenario_type = _get_scenario_type(scenario)

    # Setup environment (load fixtures)
    context: Optional[EnvironmentContext] = None
    try:
        context = await env.setup(scenario)
    except Exception as e:
        _logger.debug("Environment setup failed: %s", e)
        return {
            "scenario": scenario.get("name", "scenario"),
            "type": scenario_type,
            "error": {"type": "setup_failed", "message": str(e)},
            "measures": [],
        }

    try:
        # Apply environment-declared env vars BEFORE loading the agent
        # spec: spec loading reads process env — notably XDG_CONFIG_HOME
        # for ``allow_user_mcp_servers``' mcp.json discovery — so an
        # environment that stages per-scenario config (e.g. an isolated
        # XDG_CONFIG_HOME with a generated mcp.json) must have its vars
        # visible now, not after the spec exists. Originals are saved and
        # restored in the ``finally`` below. There is no await between
        # here and the spec load, so under asyncio concurrency each
        # scenario's spec load reads its own values.
        if context:
            env_vars = env.get_env_vars(context)
            for var_name, var_value in env_vars.items():
                saved_env_vars[var_name] = os.environ.get(var_name)
                os.environ[var_name] = str(var_value)

        # Load agent spec with project registries so all tools/server_functions
        # /capabilities resolve.
        _tool_reg, _sf_reg, _cap_reg = _build_registries_for_eval(
            Path(agent_yaml), project_root=project_root
        )
        # Honor ``allow_user_mcp_servers`` in project.yml so evals see the
        # same MCP surface as production. Without this, ``mcp.json`` under
        # ``$XDG_CONFIG_HOME/<project>/`` is invisible to the agent during
        # eval runs (which is what blocks WorkspaceEvalEnvironment's
        # db_fixture mcp.json from reaching the agent).
        _user_mcp = _load_user_mcp_for_eval(Path(agent_yaml), project_root)
        spec = load_agent_spec_from_yaml(
            agent_yaml,
            tool_registry=_tool_reg if _tool_reg else None,
            server_functions_registry=_sf_reg if _sf_reg else None,
            capability_registry=_cap_reg if _cap_reg else None,
            user_mcp_servers=_user_mcp if _user_mcp else None,
        )
        spec.use_safe_agent = False

        # Per-scenario session variables (e.g. additional_instructions).
        # Mirrors the launcher's start_run(variables=...) dispatch.
        _apply_scenario_variables(spec, scenario)

        if "max_turns" in defaults:
            spec.max_turns = int(defaults.get("max_turns", spec.max_turns))

        # Anchor builtin filesystem tools to the eval invocation's cwd.
        # Without this, start_run raises WorkspaceRootRequiredError under
        # strict mode (no default_workspace_root is set on the eval-side
        # AgentService). Mirrors the judges path in _run_judges.
        if not isinstance(spec.context, dict):
            spec.context = {} if spec.context is None else {"_original": spec.context}
        spec.context.setdefault("workspace_root", os.getcwd())

        # Inject environment context into spec.context for tools
        if context and context.data:
            spec.context.update(context.data)

        # Let the environment wire runtime-only objects into the spec while
        # it's still mutable — e.g. attach a live SandboxSession and flip
        # sandbox_use_sandbox_agent so the builder constructs a SandboxAgent
        # whose tools exec inside a container. No-op for environments that
        # don't override prepare_spec. Runs after the context.data merge
        # above so it can read anything setup() stashed in the context.
        await env.prepare_spec(spec, context)

        # Create the agent
        agent = await create_unified_agent(spec)

        # Route based on scenario type
        base_dir = Path(agent_yaml).parent
        if scenario_type == "synthetic":
            return await _run_synthetic_scenario(
                agent,
                spec,
                scenario,
                context,
                env,
                defaults,
                base_dir,
                workflow_name,
                studio_trace,
            )
        elif _is_scripted_multi_turn(scenario):
            return await _run_scripted_multi_turn(
                agent,
                spec,
                scenario,
                context,
                env,
                workflow_name,
                studio_trace,
                eval_cfg=eval_cfg,
                agent_yaml=agent_yaml,
                eval_dir=eval_dir,
            )
        else:
            return await _run_scripted_single_turn(
                agent,
                spec,
                scenario,
                context,
                env,
                workflow_name,
                studio_trace,
                eval_cfg=eval_cfg,
                agent_yaml=agent_yaml,
                eval_dir=eval_dir,
            )

    except Exception as e:
        _logger.debug("Scenario failed: %s", e)
        return {
            "scenario": scenario.get("name", "scenario"),
            "type": scenario_type,
            "error": {"type": "run_failed", "message": str(e)},
            "measures": [],
        }

    finally:
        # Restore original environment variables for parallel execution safety
        for var_name, original_value in saved_env_vars.items():
            if original_value is None:
                os.environ.pop(var_name, None)
            else:
                os.environ[var_name] = original_value

        # Clean up agent
        if agent is not None:
            cleanup = getattr(agent, "cleanup", None)
            if callable(cleanup):
                try:
                    result = cleanup()
                    if inspect.isawaitable(result):
                        await result
                except Exception as e:
                    _logger.debug("Agent cleanup failed: %s", e)

        # Always teardown environment
        if context:
            try:
                await env.teardown(context)
            except Exception as e:
                _logger.warning("Environment teardown failed: %s", e)


async def _run_judges_for_scenario(
    agent_yaml: str,
    eval_cfg: Dict[str, Any],
    scenario: Dict[str, Any],
    history: List[Dict[str, Any]],
    eval_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run LLM judges for a scenario and return metadata with results.

    Delegates to the basic pipeline's ``_run_judges`` so that the
    extended pipeline (environment-backed) gets the same judge support
    as the basic one. ``eval_dir`` must be threaded through so that the
    judge's ``instructions_file`` and ``template_file`` resolve relative
    to the evaluation config directory rather than the agent yaml's
    parent (which would silently fall back to default empty templates).
    """
    try:
        from .pipeline import _run_judges

        return await _run_judges(
            Path(agent_yaml), eval_cfg, scenario, history, eval_dir=eval_dir
        )
    except Exception as exc:
        _logger.debug("Judge execution failed: %s", exc)
        return {}


async def _run_scripted_single_turn(
    agent: Any,
    spec: Any,
    scenario: Dict[str, Any],
    context: Optional[EnvironmentContext],
    env: EvalEnvironment,
    workflow_name: Optional[str] = None,
    studio_trace: Optional[Any] = None,
    eval_cfg: Optional[Dict[str, Any]] = None,
    agent_yaml: Optional[str] = None,
    eval_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run a scripted single-turn scenario (prompt: "...")."""
    prompt = scenario["prompt"]  # Required for single-turn
    msgs = [{"role": "user", "content": prompt}]
    run_uid = None
    stream_telemetry = {
        "usage": {
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
    }

    with scenario_timer() as elapsed_fn:
        if workflow_name:
            import uuid as _uuid

            try:
                _sid = scenario.get("id") or scenario.get("name") or "scenario"
                run_uid = f"{_sid}-{_uuid.uuid4().hex[:8]}"
            except Exception:
                run_uid = _uuid.uuid4().hex[:8]
            expected_block = scenario.get("expect")
            meta = {
                "omniagents": {
                    "run_type": "evaluation",
                    "scenario_type": "scripted_single_turn",
                    "scenario": {
                        "id": scenario.get("id"),
                        "name": scenario.get("name"),
                    },
                    "run": {"id": run_uid},
                    **({"expected": expected_block} if expected_block else {}),
                }
            }
            run_config = RunConfig(workflow_name=workflow_name, group_id=run_uid)
            if studio_trace:
                with studio_trace(workflow_name, group_id=run_uid, metadata=meta):
                    result = agent.run_streamed(msgs, run_config=run_config)
                    stream_telemetry = await consume_stream_events(
                        result.stream_events()
                    )
            else:
                result = agent.run_streamed(msgs, run_config=run_config)
                stream_telemetry = await consume_stream_events(result.stream_events())
        else:
            result = agent.run_streamed(msgs)
            stream_telemetry = await consume_stream_events(result.stream_events())

    session_id = getattr(agent, "_session_id", None)
    history = (
        load_history(
            session_id,
            project_slug=spec.project_slug,
            agent_slug=spec.agent_slug,
        )
        if session_id
        else []
    )

    # Run LLM judges before measures so judge results are available via ctx.judge_result()
    judge_metadata = {}
    if agent_yaml and eval_cfg:
        judge_metadata = await _run_judges_for_scenario(
            agent_yaml, eval_cfg, scenario, history, eval_dir=eval_dir
        )

    ctx_metadata = {
        "environment_context": context.data if context else {},
        "scenario": scenario,
        **judge_metadata,
    }
    eval_ctx = build_eval_context(
        history=history,
        tools=spec.available_tools,
        metadata=ctx_metadata,
    )
    eval_ctx._environment = env
    eval_ctx._environment_context = context

    from omniagents.core.eval.measure_tiers import parse_measures, tag_results

    _measure_names, _tier_by_name = parse_measures(scenario.get("measures"))
    measures_out = (
        run_measures(eval_ctx, measures=_measure_names) if _measure_names else []
    )
    tag_results(measures_out, _tier_by_name)

    result_metadata = {
        "environment_context": context.data if context else {},
        **judge_metadata,
    }
    if run_uid:
        result_metadata["group_id"] = run_uid

    telemetry = build_telemetry(
        elapsed_fn=elapsed_fn,
        history=history,
        stream_result=stream_telemetry,
    )

    from .transcripts import export_transcript, resolve_project_root

    transcript_path = export_transcript(
        history,
        scenario,
        project_root=resolve_project_root(agent_yaml) if agent_yaml else Path.cwd(),
        session_id=session_id,
    )

    return {
        "scenario": scenario.get("name", "scenario"),
        "type": "scripted",
        "prompt": prompt,
        "session_id": session_id,
        "final_assistant_text": final_assistant_text(history),
        "measures": measures_out,
        "history_len": len(history),
        "exchanges": 1,
        "metadata": result_metadata,
        "transcript_path": transcript_path,
        **telemetry,
    }


async def _run_scripted_multi_turn(
    agent: Any,
    spec: Any,
    scenario: Dict[str, Any],
    context: Optional[EnvironmentContext],
    env: EvalEnvironment,
    workflow_name: Optional[str] = None,
    studio_trace: Optional[Any] = None,
    eval_cfg: Optional[Dict[str, Any]] = None,
    agent_yaml: Optional[str] = None,
    eval_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run a scripted multi-turn scenario.

    Recognised scenario shapes:
      - ``prompts: [...]`` — replay each prompt in order, one turn per entry
      - ``prompt: "..."`` + ``max_continuations: N`` — loop a single prompt
      - ``continuation_prompt: "..."`` — substituted for any turn beyond the
        scripted prompts (mirrors the supervisor "initial vs continuation"
        split)
      - ``until_sentinel: "..."`` — break early when the agent's last
        assistant text contains the substring
    """
    # Normalise the prompt list. Either ``prompts`` (list) or ``prompt``
    # (single string) is accepted; any extra turns beyond the scripted
    # prompts are filled with ``continuation_prompt`` (or the last prompt).
    if "prompts" in scenario and isinstance(scenario["prompts"], list):
        prompts: List[str] = list(scenario["prompts"])
    elif "prompt" in scenario:
        prompts = [scenario["prompt"]]
    else:
        raise ValueError(
            "scripted multi-turn scenario requires 'prompts' (list) or 'prompt' (string)"
        )
    if not prompts:
        raise ValueError("scripted multi-turn scenario has empty prompt list")

    max_continuations = int(scenario.get("max_continuations") or len(prompts))
    if max_continuations < len(prompts):
        max_continuations = len(prompts)
    continuation_prompt = scenario.get("continuation_prompt")
    until_sentinel = scenario.get("until_sentinel")
    until_sql = scenario.get("until_sql")
    # ``until_sql`` reads the SQLite path the environment staged in its
    # context (e.g. WorkspaceEvalEnvironment writes ``db_path`` when
    # ``db_fixture`` is set). The query runs read-only after each turn;
    # any row returned counts as the predicate firing.
    until_sql_db_path = (
        (context.data.get("db_path") if context and context.data else None)
        if until_sql
        else None
    )
    if until_sql and not until_sql_db_path:
        _logger.warning(
            "scenario %r has until_sql but environment did not stage db_path; "
            "predicate will never fire",
            scenario.get("name"),
        )

    session_id = None
    run_uid = None
    cumulative_usage = {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }

    def _accumulate(st):
        u = st.get("usage", {})
        for k in cumulative_usage:
            cumulative_usage[k] += u.get(k, 0)

    use_trace = bool(workflow_name and studio_trace)
    run_config = None
    meta = None
    if workflow_name:
        import uuid as _uuid

        try:
            _sid = scenario.get("id") or scenario.get("name") or "scenario"
            run_uid = f"{_sid}-{_uuid.uuid4().hex[:8]}"
        except Exception:
            run_uid = _uuid.uuid4().hex[:8]
        run_config = RunConfig(workflow_name=workflow_name, group_id=run_uid)
        if use_trace:
            expected_block = scenario.get("expect")
            meta = {
                "omniagents": {
                    "run_type": "evaluation",
                    "scenario_type": "scripted_multi_turn",
                    "scenario": {
                        "id": scenario.get("id"),
                        "name": scenario.get("name"),
                    },
                    "run": {"id": run_uid},
                    "exchanges": max_continuations,
                    **({"expected": expected_block} if expected_block else {}),
                }
            }

    break_reason = "max_continuations"
    turns_completed = 0

    async def _run_one_turn(prompt_text: str) -> None:
        nonlocal session_id
        msgs = [{"role": "user", "content": prompt_text}]
        if run_config:
            stream_result = agent.run_streamed(msgs, run_config=run_config)
        else:
            stream_result = agent.run_streamed(msgs)
        _accumulate(await consume_stream_events(stream_result.stream_events()))
        session_id = getattr(agent, "_session_id", None)

    def _sentinel_hit() -> bool:
        if not until_sentinel or session_id is None:
            return False
        # Reload only what we need to check the last message. Cheap because
        # session histories during eval runs are small.
        recent = load_history(
            session_id,
            project_slug=spec.project_slug,
            agent_slug=spec.agent_slug,
        )
        return until_sentinel in (final_assistant_text(recent) or "")

    def _sql_predicate_hit() -> bool:
        if not until_sql or not until_sql_db_path:
            return False
        import sqlite3

        try:
            conn = sqlite3.connect(
                f"file:{until_sql_db_path}?mode=ro", uri=True, timeout=2.0
            )
            try:
                row = conn.execute(until_sql).fetchone()
                return row is not None
            finally:
                conn.close()
        except sqlite3.Error as exc:
            _logger.warning(
                "until_sql query failed for scenario %r: %s",
                scenario.get("name"),
                exc,
            )
            return False

    async def _drive_loop() -> None:
        nonlocal break_reason, turns_completed
        for turn_idx in range(max_continuations):
            if turn_idx < len(prompts):
                prompt_text = prompts[turn_idx]
            else:
                prompt_text = continuation_prompt or prompts[-1]
            await _run_one_turn(prompt_text)
            turns_completed = turn_idx + 1
            if _sentinel_hit():
                break_reason = "sentinel"
                return
            if _sql_predicate_hit():
                break_reason = "sql_predicate"
                return

    with scenario_timer() as elapsed_fn:
        if use_trace:
            with studio_trace(workflow_name, group_id=run_uid, metadata=meta):
                await _drive_loop()
        else:
            await _drive_loop()

    stream_telemetry = {"usage": cumulative_usage}

    history = (
        load_history(
            session_id,
            project_slug=spec.project_slug,
            agent_slug=spec.agent_slug,
        )
        if session_id
        else []
    )

    # Run LLM judges before measures
    judge_metadata = {}
    if agent_yaml and eval_cfg:
        judge_metadata = await _run_judges_for_scenario(
            agent_yaml, eval_cfg, scenario, history, eval_dir=eval_dir
        )

    ctx_metadata = {
        "environment_context": context.data if context else {},
        "scenario": scenario,
        **judge_metadata,
    }
    eval_ctx = build_eval_context(
        history=history,
        tools=spec.available_tools,
        metadata=ctx_metadata,
    )
    eval_ctx._environment = env
    eval_ctx._environment_context = context

    from omniagents.core.eval.measure_tiers import parse_measures, tag_results

    _measure_names, _tier_by_name = parse_measures(scenario.get("measures"))
    measures_out = (
        run_measures(eval_ctx, measures=_measure_names) if _measure_names else []
    )
    tag_results(measures_out, _tier_by_name)

    result_metadata = {
        "environment_context": context.data if context else {},
        **judge_metadata,
    }
    if run_uid:
        result_metadata["group_id"] = run_uid
    result_metadata["break_reason"] = break_reason
    result_metadata["turns_completed"] = turns_completed
    result_metadata["max_continuations"] = max_continuations

    telemetry = build_telemetry(
        elapsed_fn=elapsed_fn,
        history=history,
        stream_result=stream_telemetry,
    )

    from .transcripts import export_transcript, resolve_project_root

    transcript_path = export_transcript(
        history,
        scenario,
        project_root=resolve_project_root(agent_yaml) if agent_yaml else Path.cwd(),
        session_id=session_id,
    )

    return {
        "scenario": scenario.get("name", "scenario"),
        "type": "scripted",
        "prompts": prompts,
        "session_id": session_id,
        "final_assistant_text": final_assistant_text(history),
        "measures": measures_out,
        "history_len": len(history),
        "exchanges": turns_completed,
        "break_reason": break_reason,
        "metadata": result_metadata,
        "transcript_path": transcript_path,
        **telemetry,
    }


async def _run_synthetic_scenario(
    agent: Any,
    spec: Any,
    scenario: Dict[str, Any],
    context: Optional[EnvironmentContext],
    env: EvalEnvironment,
    defaults: Dict[str, Any],
    base_dir: Path,
    workflow_name: Optional[str] = None,
    studio_trace: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run a synthetic scenario with simulated user (type: synthetic)."""
    # max_exchanges controls conversation length (not agent's max_turns)
    max_exchanges = scenario.get("max_exchanges", defaults.get("max_exchanges", 10))

    # Create run configs for agent and simulated user (separate group_ids for distinct traces)
    agent_run_config = None
    user_run_config = None
    agent_run_uid = None

    if workflow_name:
        import uuid as _uuid

        try:
            _sid = scenario.get("id") or scenario.get("name") or "scenario"
            agent_run_uid = f"{_sid}-agent-{_uuid.uuid4().hex[:8]}"
            user_run_uid = f"{_sid}-user-{_uuid.uuid4().hex[:8]}"
        except Exception:
            agent_run_uid = f"agent-{_uuid.uuid4().hex[:8]}"
            user_run_uid = f"user-{_uuid.uuid4().hex[:8]}"

        agent_run_config = RunConfig(
            workflow_name=workflow_name, group_id=agent_run_uid
        )
        user_run_config = RunConfig(
            workflow_name=f"{workflow_name}_simulated_user", group_id=user_run_uid
        )

    # Create simulated user from scenario (with its own run_config)
    persona = UserPersona.from_scenario(scenario, base_dir)
    agent_description = spec.description or spec.name or "an AI assistant"
    simulated_user = SimulatedUser(
        persona=persona,
        agent_description=agent_description,
        model=scenario.get("model", "gpt-4.1"),
        run_config=user_run_config,
    )

    cumulative_usage = {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }

    with scenario_timer() as elapsed_fn:
        if workflow_name and studio_trace:
            import uuid as _uuid

            expected_block = scenario.get("expect")
            meta = {
                "omniagents": {
                    "run_type": "evaluation",
                    "scenario_type": "synthetic",
                    "scenario": {
                        "id": scenario.get("id"),
                        "name": scenario.get("name"),
                        "goal": scenario.get("goal"),
                    },
                    "run": {
                        "id": agent_run_config.group_id if agent_run_config else None
                    },
                    "max_exchanges": max_exchanges,
                    **({"expected": expected_block} if expected_block else {}),
                }
            }
            with studio_trace(
                workflow_name, group_id=agent_run_config.group_id, metadata=meta
            ):
                history = await _run_synthetic_conversation(
                    agent,
                    spec,
                    simulated_user,
                    max_exchanges,
                    run_config=agent_run_config,
                    usage_accumulator=cumulative_usage,
                )
        else:
            history = await _run_synthetic_conversation(
                agent,
                spec,
                simulated_user,
                max_exchanges,
                run_config=agent_run_config,
                usage_accumulator=cumulative_usage,
            )

    stream_telemetry = {"usage": cumulative_usage}
    session_id = getattr(agent, "_session_id", None)

    eval_ctx = build_eval_context(
        history=history,
        tools=spec.available_tools,
        metadata={
            "environment_context": context.data if context else {},
            "user_completed": simulated_user.completed,
            "user_completion_reason": simulated_user.completion_reason,
            "scenario": scenario,
        },
    )
    eval_ctx._environment = env
    eval_ctx._environment_context = context

    from omniagents.core.eval.measure_tiers import parse_measures, tag_results

    _measure_names, _tier_by_name = parse_measures(scenario.get("measures"))
    measures_out = (
        run_measures(eval_ctx, measures=_measure_names) if _measure_names else []
    )
    tag_results(measures_out, _tier_by_name)

    # Count exchanges from history
    exchanges = sum(1 for m in history if m.get("role") == "user")

    result_metadata = {
        "environment_context": context.data if context else {},
    }
    if agent_run_uid:
        result_metadata["group_id"] = agent_run_uid

    telemetry = build_telemetry(
        elapsed_fn=elapsed_fn,
        history=history,
        stream_result=stream_telemetry,
    )

    from .transcripts import export_transcript, resolve_project_root

    transcript_path = export_transcript(
        history,
        scenario,
        project_root=resolve_project_root(base_dir),
        session_id=session_id,
    )

    return {
        "scenario": scenario.get("name", "scenario"),
        "type": "synthetic",
        "goal": scenario["goal"],
        "session_id": session_id,
        "final_assistant_text": final_assistant_text(history),
        "measures": measures_out,
        "history_len": len(history),
        "exchanges": exchanges,
        "user_completed": simulated_user.completed,
        "user_completion_reason": simulated_user.completion_reason,
        "metadata": result_metadata,
        "transcript_path": transcript_path,
        **telemetry,
    }


async def _run_synthetic_conversation(
    agent: Any,
    spec: Any,
    user: SimulatedUser,
    max_exchanges: int,
    run_config: Optional[RunConfig] = None,
    usage_accumulator: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    """Run a synthetic conversation between agent and simulated user.

    Each exchange:
    1. User sends a message
    2. Agent responds (may involve multiple tool calls)
    3. User responds to agent's output
    4. Repeat until user signals completion or max_exchanges reached

    Returns the full message history from the agent's session.
    """
    # Get initial user message
    user_message = await user.get_initial_message()

    for _exchange in range(max_exchanges):
        # Run agent with user message
        msgs = [{"role": "user", "content": user_message}]
        if run_config:
            result = agent.run_streamed(msgs, run_config=run_config)
        else:
            result = agent.run_streamed(msgs)
        st = await consume_stream_events(result.stream_events())
        if usage_accumulator is not None:
            u = st.get("usage", {})
            for k in usage_accumulator:
                usage_accumulator[k] += u.get(k, 0)

        # Check if user already signaled completion
        if user.completed:
            break

        # Get assistant's response text
        assistant_text = final_assistant_text(
            load_history(
                getattr(agent, "_session_id", None),
                project_slug=getattr(spec, "project_slug", None),
                agent_slug=getattr(spec, "agent_slug", None),
            )
            if getattr(agent, "_session_id", None)
            else []
        )

        if not assistant_text:
            break

        # Get user's response to assistant
        user_message = await user.respond(assistant_text)

        if user.completed:
            break

    # Return final history
    session_id = getattr(agent, "_session_id", None)
    return (
        load_history(
            session_id,
            project_slug=getattr(spec, "project_slug", None),
            agent_slug=getattr(spec, "agent_slug", None),
        )
        if session_id
        else []
    )


def _is_synthetic_scenario(scenario: Dict[str, Any]) -> bool:
    """Check if scenario is synthetic type."""
    return scenario.get("type") == "synthetic"


async def run_evaluation_with_environment_async(
    compiled_yaml: Path,
    environment: Optional[EvalEnvironment] = None,
    workflow_name: str | None = None,
    include_unmeasured: bool = False,
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    concurrency: int = 5,
    eval_config: Any | None = None,
) -> Dict[str, Any]:
    """Run evaluation with environment support.

    Supports all scenario types:
    - Scripted single-turn: `prompt: "..."`
    - Scripted multi-turn: `prompts: ["...", "..."]`
    - Synthetic: `type: synthetic` with `goal: "..."`

    Args:
        compiled_yaml: Path to compiled evaluation YAML.
        environment: Optional EvalEnvironment instance.
        workflow_name: Optional workflow name for tracing.
        include_unmeasured: Include scenarios without measures.
        progress_cb: Progress callback function.
        concurrency: Maximum concurrent scenarios.
        eval_config: Optional EvalConfig from the runtime loader.

    Returns:
        Evaluation results dict.
    """
    # When an EvalConfig is provided, use it directly
    if eval_config is not None:
        cfg_path = eval_config.agent_path
        eval_cfg = eval_config.config
        _project_root = eval_config.project_root
        _eval_dir = eval_config.eval_dir
    else:
        cfg_path = Path(compiled_yaml).resolve()
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        eval_cfg = (raw or {}).get("evaluation") or {}
        _project_root = None
        _eval_dir = None
    defaults = eval_cfg.get("defaults") or {}
    scenarios = eval_cfg.get("scenarios") or []

    # Initialize tracing if workflow_name is provided
    studio_trace = None
    if workflow_name:
        try:
            from omniagents.studio.tracing import trace as _studio_trace

            studio_trace = _studio_trace
        except ImportError:
            _logger.debug("omniagents.studio.tracing not available")
            studio_trace = None

    # Load local measures from project
    from .pipeline import _load_local_measures

    _load_local_measures(cfg_path, project_root=_project_root, eval_dir=_eval_dir)

    # Build the declared environment if one isn't provided
    env_config = eval_cfg.get("environment")
    env_load_error: Optional[str] = None
    if environment is None and env_config:
        try:
            environment = build_environment(
                env_config,
                project_root=_project_root or cfg_path.parent,
                eval_dir=_eval_dir,
            )
        except Exception as exc:
            env_load_error = f"Failed to build declared environment: {exc}"
            _logger.error(env_load_error)

    # Filter scenarios
    if not include_unmeasured:
        from omniagents.core.eval.measure_tiers import measure_names

        scenarios = [sc for sc in scenarios if measure_names(sc.get("measures"))]

    all_runs: List[Dict[str, Any]] = []
    _peek_default_runs_per = int((defaults or {}).get("runs_per_prompt", 1) or 1)
    total_runs = sum(
        max(1, int(sc.get("runs_per_prompt") or _peek_default_runs_per))
        for sc in scenarios
    )

    if progress_cb:
        try:
            progress_cb({"event": "start", "total": total_runs})
        except Exception:
            pass

    # Parallel execution is now safe with environments because run_scenario
    # saves and restores env vars around each scenario execution
    sem = asyncio.Semaphore(concurrency)
    completed_count = 0

    async def _worker(sc):
        nonlocal completed_count
        async with sem:
            # Use run_scenario for all scenarios when environment is present
            # or for synthetic/multi-turn scenarios
            if environment or _is_synthetic_scenario(sc) or _is_scripted_multi_turn(sc):
                r = await run_scenario(
                    str(cfg_path),
                    sc,
                    environment,
                    defaults,
                    eval_cfg,
                    workflow_name,
                    studio_trace,
                    project_root=_project_root,
                    eval_dir=_eval_dir,
                )
            else:
                # Use existing single-turn pipeline (no environment, scripted)
                from .pipeline import _run_scenario

                r = await _run_scenario(
                    str(cfg_path),
                    sc,
                    defaults,
                    eval_cfg,
                    workflow_name,
                    project_root=_project_root,
                    eval_dir=_eval_dir,
                )

            r["scenario_config"] = sc
            completed_count += 1

            if progress_cb:
                try:
                    measures = r.get("measures") or []
                    failed = sum(1 for m in measures if m.get("passed") is False)
                    passed = sum(1 for m in measures if m.get("passed") is True)
                    progress_cb(
                        {
                            "event": "progress",
                            "index": completed_count,
                            "total": total_runs,
                            "scenario": r.get("scenario"),
                            "failed": failed,
                            "passed": passed,
                        }
                    )
                except Exception:
                    pass

            return r

    # Honor runs_per_prompt the same way the basic pipeline does. Per-scenario
    # ``runs_per_prompt:`` overrides the defaults block — lets a single
    # non-deterministic scenario be loud (N=10) without slowing the rest of
    # the suite.
    default_runs_per = int(defaults.get("runs_per_prompt", 1) or 1)
    tasks = []
    for sc in scenarios:
        sc_runs = int(sc.get("runs_per_prompt") or default_runs_per)
        for _ in range(max(1, sc_runs)):
            tasks.append(asyncio.create_task(_worker(sc)))

    if tasks:
        all_runs = await asyncio.gather(*tasks)
    else:
        all_runs = []

    # Compute metrics
    from .metrics import compute_metrics

    metrics_cfg = eval_cfg.get("metrics") or []
    metrics = compute_metrics(all_runs, metrics_cfg)

    # Count scenario types
    synthetic_count = sum(1 for sc in scenarios if _is_synthetic_scenario(sc))
    scripted_multi_count = sum(1 for sc in scenarios if _is_scripted_multi_turn(sc))

    result = {
        "agent_yaml": str(cfg_path),
        "runs": all_runs,
        "metrics": metrics,
        "stats": {
            "total_scenarios": len(scenarios),
            "synthetic_scenarios": synthetic_count,
            "scripted_multi_turn_scenarios": scripted_multi_count,
        },
    }

    # Include environment loading error if present
    if env_load_error:
        result["environment_error"] = env_load_error
        result["warnings"] = result.get("warnings", []) + [
            f"Environment loading failed: {env_load_error}"
        ]

    return result


async def run_scenarios_against_agent(
    agent_yaml: Path,
    scenarios: List[Dict[str, Any]],
    max_turns: int | None = None,
    include_history: bool = False,
    workflow_name: str | None = None,
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    scenario_source_path: Optional[Path] = None,
    concurrency: int = 5,
    eval_config: Any | None = None,
) -> List[Dict[str, Any]]:
    """Run scenarios against an agent with full environment and measures support.

    This is the primary function for running evaluation scenarios. It supports:
    - Scripted single-turn: `prompt: "..."`
    - Scripted multi-turn: `prompts: ["...", "..."]`
    - Synthetic: `type: synthetic` with `goal: "..."`

    Args:
        agent_yaml: Path to the agent YAML configuration.
        scenarios: List of scenario configurations to run.
        max_turns: Optional max turns override for agent.
        include_history: Whether to include full history in results.
        workflow_name: Optional workflow name for tracing.
        progress_cb: Optional progress callback function.
        scenario_source_path: Optional path to the scenario source file.
        concurrency: Maximum number of scenarios to run in parallel (default 5).
        eval_config: Optional EvalConfig from the runtime loader.

    Returns:
        List of scenario run results with measures, history, etc.
    """
    if eval_config is not None:
        cfg_path = eval_config.agent_path
        eval_cfg = eval_config.config
        _project_root = eval_config.project_root
        _eval_dir = eval_config.eval_dir
    else:
        cfg_path = Path(agent_yaml).resolve()
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        eval_cfg = (raw or {}).get("evaluation") or {}
        _project_root = None
        _eval_dir = None
    defaults = eval_cfg.get("defaults") or {}

    # Override max_turns if provided
    if max_turns is not None:
        defaults = {**defaults, "max_turns": max_turns}

    # Initialize tracing if workflow_name is provided
    studio_trace = None
    if workflow_name:
        try:
            from omniagents.studio.tracing import trace as _studio_trace

            studio_trace = _studio_trace
        except ImportError:
            _logger.debug("omniagents.studio.tracing not available")
            studio_trace = None

    # Load local measures from project
    from .pipeline import _load_local_measures

    _load_local_measures(cfg_path, project_root=_project_root, eval_dir=_eval_dir)

    # Build the declared environment from config
    environment = None
    env_config = eval_cfg.get("environment")
    if env_config:
        try:
            environment = build_environment(
                env_config,
                project_root=_project_root or cfg_path.parent,
                eval_dir=_eval_dir,
            )
        except Exception as exc:
            _logger.error("Failed to build declared environment: %s", exc)

    total = len(scenarios)

    if progress_cb:
        try:
            progress_cb({"event": "start", "total": total})
        except Exception:
            pass

    # Shared state for progress tracking
    completed_count = 0
    failed_count = 0
    results_lock = asyncio.Lock()

    sem = asyncio.Semaphore(concurrency)

    async def _run_single(idx: int, sc: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal completed_count, failed_count

        async with sem:
            # Add source path to scenario if provided
            if scenario_source_path and "_source_path" not in sc:
                sc["_source_path"] = str(scenario_source_path)

            # Run the scenario using the full implementation
            run_result = await run_scenario(
                str(cfg_path),
                sc,
                environment,
                defaults,
                eval_cfg,
                workflow_name,
                studio_trace,
                project_root=_project_root,
                eval_dir=_eval_dir,
            )

            # Transform result to match legacy format
            result = {
                "scenario_id": sc.get("id"),
                "scenario_name": sc.get("name") or run_result.get("scenario"),
                "prompt": sc.get("prompt") or run_result.get("prompt"),
                "session_id": run_result.get("session_id"),
                "final_assistant_text": run_result.get("final_assistant_text", ""),
                "history_len": run_result.get("history_len", 0),
                "measures": run_result.get("measures", []),
                "type": run_result.get("type", "scripted"),
                "exchanges": run_result.get("exchanges", 1),
                "metadata": run_result.get("metadata"),
            }

            # Include history if requested (would need to load from session)
            if include_history and run_result.get("session_id"):
                from omniagents.core.session.history_db import load_history
                from omniagents.core.config.loader import load_agent_spec_from_yaml

                spec = load_agent_spec_from_yaml(str(cfg_path))
                history = load_history(
                    run_result["session_id"],
                    project_slug=spec.project_slug,
                    agent_slug=spec.agent_slug,
                )
                result["history"] = history

            if scenario_source_path:
                result["scenario_path"] = str(scenario_source_path)

            if run_result.get("error"):
                result["error"] = run_result["error"]

            # Include synthetic-specific fields
            if run_result.get("type") == "synthetic":
                result["goal"] = run_result.get("goal")
                result["user_completed"] = run_result.get("user_completed")
                result["user_completion_reason"] = run_result.get(
                    "user_completion_reason"
                )

            # Update progress
            async with results_lock:
                completed_count += 1
                if run_result.get("error"):
                    failed_count += 1

                if progress_cb:
                    try:
                        ev = {
                            "event": "progress",
                            "current": completed_count,
                            "total": total,
                            "scenario_id": sc.get("id"),
                            "scenario": run_result.get("scenario"),
                            "passed": sum(
                                1
                                for m in run_result.get("measures", [])
                                if m.get("passed")
                            ),
                            "failed": sum(
                                1
                                for m in run_result.get("measures", [])
                                if m.get("passed") is False
                            ),
                        }
                        if failed_count:
                            ev["failed_scenarios"] = failed_count
                        progress_cb(ev)
                    except Exception:
                        pass

            return result

    # Honor runs_per_prompt (defaults block, with per-scenario override) so
    # callers of run_scenarios_against_agent see the same multi-run behaviour
    # as the suite-level CLI.
    default_runs_per = int(defaults.get("runs_per_prompt", 1) or 1)
    expanded: List[Tuple[int, Dict[str, Any]]] = []
    for idx, sc in enumerate(scenarios):
        sc_runs = int(sc.get("runs_per_prompt") or default_runs_per)
        for _ in range(max(1, sc_runs)):
            expanded.append((idx, sc))
    # Run all scenarios in parallel with concurrency limit
    tasks = [_run_single(idx, sc) for idx, sc in expanded]
    results = await asyncio.gather(*tasks)

    if progress_cb:
        try:
            done_payload = {"event": "done", "count": len(results)}
            if failed_count:
                done_payload["failed"] = failed_count
            progress_cb(done_payload)
        except Exception:
            pass

    return list(results)
