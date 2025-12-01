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
from typing import Any, Callable, Dict, List, Optional, Type

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
from .simulated_user import SimulatedUser, UserPersona

__all__ = [
    "run_scenario",
    "run_scenarios_against_agent",
    "run_evaluation_with_environment_async",
    "load_environment_class",
]

_logger = logging.getLogger(__name__)


def _get_scenario_type(scenario: Dict[str, Any]) -> str:
    """Determine scenario type from configuration.

    Returns:
        'scripted' (default) or 'synthetic'
    """
    return scenario.get("type", "scripted")


def _is_scripted_multi_turn(scenario: Dict[str, Any]) -> bool:
    """Check if scenario is scripted multi-turn (has prompts list)."""
    return "prompts" in scenario and isinstance(scenario["prompts"], list)


async def run_scenario(
    agent_yaml: str,
    scenario: Dict[str, Any],
    environment: Optional[EvalEnvironment] = None,
    defaults: Optional[Dict[str, Any]] = None,
    eval_cfg: Optional[Dict[str, Any]] = None,
    workflow_name: Optional[str] = None,
    studio_trace: Optional[Any] = None,
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
        # Load agent spec
        spec = load_agent_spec_from_yaml(agent_yaml)
        spec.use_safe_agent = False

        if "max_turns" in defaults:
            spec.max_turns = int(defaults.get("max_turns", spec.max_turns))

        # Inject environment context into spec.context for tools
        if context and context.data:
            if spec.context is None:
                spec.context = {}
            elif not isinstance(spec.context, dict):
                spec.context = {"_original": spec.context}
            spec.context.update(context.data)

            # Set env vars as specified by the environment
            # Save original values so we can restore them for parallel execution
            env_vars = env.get_env_vars(context)
            for var_name, var_value in env_vars.items():
                saved_env_vars[var_name] = os.environ.get(var_name)
                os.environ[var_name] = str(var_value)

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
                agent, spec, scenario, context, env, workflow_name, studio_trace
            )
        else:
            return await _run_scripted_single_turn(
                agent, spec, scenario, context, env, workflow_name, studio_trace
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


async def _run_scripted_single_turn(
    agent: Any,
    spec: Any,
    scenario: Dict[str, Any],
    context: Optional[EnvironmentContext],
    env: EvalEnvironment,
    workflow_name: Optional[str] = None,
    studio_trace: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run a scripted single-turn scenario (prompt: "...")."""
    prompt = scenario["prompt"]  # Required for single-turn
    msgs = [{"role": "user", "content": prompt}]

    if workflow_name:
        import uuid as _uuid

        try:
            _sid = scenario.get("id") or scenario.get("name") or "scenario"
            run_uid = f"{_sid}-{_uuid.uuid4().hex[:8]}"
        except Exception:
            run_uid = _uuid.uuid4().hex[:8]
        meta = {
            "omniagents": {
                "run_type": "evaluation",
                "scenario_type": "scripted_single_turn",
                "scenario": {
                    "id": scenario.get("id"),
                    "name": scenario.get("name"),
                },
                "run": {"id": run_uid},
            }
        }
        run_config = RunConfig(workflow_name=workflow_name, group_id=run_uid)
        if studio_trace:
            with studio_trace(workflow_name, group_id=run_uid, metadata=meta):
                result = agent.run_streamed(msgs, run_config=run_config)
                async for _ in result.stream_events():
                    pass
        else:
            result = agent.run_streamed(msgs, run_config=run_config)
            async for _ in result.stream_events():
                pass
    else:
        result = agent.run_streamed(msgs)
        async for _ in result.stream_events():
            pass

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

    eval_ctx = build_eval_context(
        history=history,
        tools=spec.available_tools,
        metadata={
            "environment_context": context.data if context else {},
            "scenario": scenario,
        },
    )
    eval_ctx._environment = env
    eval_ctx._environment_context = context

    measure_names = scenario.get("measures") or []
    measures_out = (
        run_measures(eval_ctx, measures=measure_names) if measure_names else []
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
        "metadata": {
            "environment_context": context.data if context else {},
        },
    }


async def _run_scripted_multi_turn(
    agent: Any,
    spec: Any,
    scenario: Dict[str, Any],
    context: Optional[EnvironmentContext],
    env: EvalEnvironment,
    workflow_name: Optional[str] = None,
    studio_trace: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run a scripted multi-turn scenario (prompts: [...])."""
    prompts = scenario["prompts"]  # Required, validated by caller
    session_id = None

    if workflow_name and studio_trace:
        import uuid as _uuid

        try:
            _sid = scenario.get("id") or scenario.get("name") or "scenario"
            run_uid = f"{_sid}-{_uuid.uuid4().hex[:8]}"
        except Exception:
            run_uid = _uuid.uuid4().hex[:8]
        meta = {
            "omniagents": {
                "run_type": "evaluation",
                "scenario_type": "scripted_multi_turn",
                "scenario": {
                    "id": scenario.get("id"),
                    "name": scenario.get("name"),
                },
                "run": {"id": run_uid},
                "exchanges": len(prompts),
            }
        }
        run_config = RunConfig(workflow_name=workflow_name, group_id=run_uid)
        with studio_trace(workflow_name, group_id=run_uid, metadata=meta):
            for prompt in prompts:
                msgs = [{"role": "user", "content": prompt}]
                result = agent.run_streamed(msgs, run_config=run_config)
                async for _ in result.stream_events():
                    pass
                session_id = getattr(agent, "_session_id", None)
    else:
        for prompt in prompts:
            msgs = [{"role": "user", "content": prompt}]
            result = agent.run_streamed(msgs)
            async for _ in result.stream_events():
                pass
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

    eval_ctx = build_eval_context(
        history=history,
        tools=spec.available_tools,
        metadata={
            "environment_context": context.data if context else {},
            "scenario": scenario,
        },
    )
    eval_ctx._environment = env
    eval_ctx._environment_context = context

    measure_names = scenario.get("measures") or []
    measures_out = (
        run_measures(eval_ctx, measures=measure_names) if measure_names else []
    )

    return {
        "scenario": scenario.get("name", "scenario"),
        "type": "scripted",
        "prompts": prompts,
        "session_id": session_id,
        "final_assistant_text": final_assistant_text(history),
        "measures": measures_out,
        "history_len": len(history),
        "exchanges": len(prompts),
        "metadata": {
            "environment_context": context.data if context else {},
        },
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

    if workflow_name and studio_trace:
        import uuid as _uuid

        meta = {
            "omniagents": {
                "run_type": "evaluation",
                "scenario_type": "synthetic",
                "scenario": {
                    "id": scenario.get("id"),
                    "name": scenario.get("name"),
                    "goal": scenario.get("goal"),
                },
                "run": {"id": agent_run_config.group_id if agent_run_config else None},
                "max_exchanges": max_exchanges,
            }
        }
        with studio_trace(
            workflow_name, group_id=agent_run_config.group_id, metadata=meta
        ):
            history = await _run_synthetic_conversation(
                agent, spec, simulated_user, max_exchanges, run_config=agent_run_config
            )
    else:
        history = await _run_synthetic_conversation(
            agent, spec, simulated_user, max_exchanges, run_config=agent_run_config
        )

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

    measure_names = scenario.get("measures") or []
    measures_out = (
        run_measures(eval_ctx, measures=measure_names) if measure_names else []
    )

    # Count exchanges from history
    exchanges = sum(1 for m in history if m.get("role") == "user")

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
        "metadata": {
            "environment_context": context.data if context else {},
        },
    }


async def _run_synthetic_conversation(
    agent: Any,
    spec: Any,
    user: SimulatedUser,
    max_exchanges: int,
    run_config: Optional[RunConfig] = None,
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
        async for _ in result.stream_events():
            pass

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


def load_environment_class(
    environment_config: Dict[str, Any],
    base_dir: Path,
) -> Optional[EvalEnvironment]:
    """Load an EvalEnvironment class from configuration.

    Args:
        environment_config: Dict with 'class' and optional 'config' keys.
        base_dir: Base directory for relative imports.

    Returns:
        Instantiated EvalEnvironment or None.
    """
    if not environment_config:
        return None

    class_path = environment_config.get("class")
    if not class_path:
        return None

    config = environment_config.get("config", {})

    try:
        # Handle relative imports from project
        if class_path.startswith("."):
            # e.g., ".environments.api_eval.ApiEvalEnvironment"
            import importlib.util
            import sys

            parts = class_path.lstrip(".").split(".")
            module_path = base_dir / "/".join(parts[:-1]) + ".py"
            class_name = parts[-1]

            spec = importlib.util.spec_from_file_location(
                f"eval_env_{parts[-2]}", str(module_path)
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                env_class = getattr(module, class_name)
                return env_class(**config)
        else:
            # Absolute import - add base_dir to sys.path if not already there
            import importlib
            import sys

            # Add base_dir to sys.path so we can import project-relative modules
            # If base_dir is "target" subdir (from compiled yaml), use parent instead
            effective_base = base_dir
            if base_dir.name == "target":
                effective_base = base_dir.parent
            base_dir_str = str(effective_base)
            if base_dir_str not in sys.path:
                sys.path.insert(0, base_dir_str)

            parts = class_path.rsplit(".", 1)
            module_name = parts[0]
            class_name = parts[1] if len(parts) > 1 else parts[0]

            module = importlib.import_module(module_name)
            env_class = getattr(module, class_name)
            return env_class(**config)

    except Exception as e:
        _logger.warning("Failed to load environment class '%s': %s", class_path, e)
        return None


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

    Returns:
        Evaluation results dict.
    """
    cfg_path = Path(compiled_yaml).resolve()
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    eval_cfg = (raw or {}).get("evaluation") or {}
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

    _load_local_measures(cfg_path)

    # Try to load environment from config if not provided
    if environment is None:
        env_config = eval_cfg.get("environment")
        if env_config:
            environment = load_environment_class(env_config, cfg_path.parent)

    # Filter scenarios
    if not include_unmeasured:
        scenarios = [sc for sc in scenarios if sc.get("measures")]

    all_runs: List[Dict[str, Any]] = []
    total_runs = len(scenarios)

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
                )
            else:
                # Use existing single-turn pipeline (no environment, scripted)
                from .pipeline import _run_scenario

                r = await _run_scenario(
                    str(cfg_path), sc, defaults, eval_cfg, workflow_name
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

    tasks = [asyncio.create_task(_worker(sc)) for sc in scenarios]

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

    return {
        "agent_yaml": str(cfg_path),
        "runs": all_runs,
        "metrics": metrics,
        "stats": {
            "total_scenarios": len(scenarios),
            "synthetic_scenarios": synthetic_count,
            "scripted_multi_turn_scenarios": scripted_multi_count,
        },
    }


async def run_scenarios_against_agent(
    agent_yaml: Path,
    scenarios: List[Dict[str, Any]],
    max_turns: int | None = None,
    include_history: bool = False,
    workflow_name: str | None = None,
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    scenario_source_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Run scenarios against an agent with full environment and measures support.

    This is the primary function for running evaluation scenarios. It supports:
    - Scripted single-turn: `prompt: "..."`
    - Scripted multi-turn: `prompts: ["...", "..."]`
    - Synthetic: `type: synthetic` with `goal: "..."`

    Args:
        agent_yaml: Path to the compiled agent YAML configuration.
        scenarios: List of scenario configurations to run.
        max_turns: Optional max turns override for agent.
        include_history: Whether to include full history in results.
        workflow_name: Optional workflow name for tracing.
        progress_cb: Optional progress callback function.
        scenario_source_path: Optional path to the scenario source file.

    Returns:
        List of scenario run results with measures, history, etc.
    """
    cfg_path = Path(agent_yaml).resolve()
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    eval_cfg = (raw or {}).get("evaluation") or {}
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

    _load_local_measures(cfg_path)

    # Load environment from config
    environment = None
    env_config = eval_cfg.get("environment")
    if env_config:
        environment = load_environment_class(env_config, cfg_path.parent)

    results: List[Dict[str, Any]] = []
    total = len(scenarios)

    if progress_cb:
        try:
            progress_cb({"event": "start", "total": total})
        except Exception:
            pass

    failed_count = 0

    for idx, sc in enumerate(scenarios):
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
        )

        # Check for errors
        if run_result.get("error"):
            failed_count += 1

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
            result["user_completion_reason"] = run_result.get("user_completion_reason")

        results.append(result)

        if progress_cb:
            try:
                ev = {
                    "event": "progress",
                    "current": idx + 1,
                    "total": total,
                    "scenario_id": sc.get("id"),
                    "scenario": run_result.get("scenario"),
                    "passed": sum(
                        1 for m in run_result.get("measures", []) if m.get("passed")
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

    if progress_cb:
        try:
            done_payload = {"event": "done", "count": len(results)}
            if failed_count:
                done_payload["failed"] = failed_count
            progress_cb(done_payload)
        except Exception:
            pass

    return results
