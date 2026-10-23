"""The evaluation engine: one implementation of running, judging and scoring.

Every way of running an evaluation goes through here - ``omniagents eval
suite run`` / ``eval scenarios run``, Studio jobs, and the notebook API
(``omniagents.notebook.EvalSuite.run``). Callers differ only in where the
scenarios come from and which :class:`~omniagents.core.eval.targets.AgentTarget`
builds the agent.

:func:`run_scenario` runs one scenario once:

1. environment ``setup`` (``NoOpEnvironment`` when none is declared) and its
   env vars, restored afterwards;
2. the target builds a fresh agent;
3. the conversation: a single ``prompt``; scripted ``prompts`` (with
   ``max_continuations`` / ``continuation_prompt`` / ``until_sentinel`` /
   ``until_sql``); or ``type: synthetic`` with a simulated user;
4. judges (``evaluation.judges``, selected per scenario), then measures
   (tier-tagged), against the final history;
5. agent cleanup and environment ``teardown``.

It returns one run record. A run that could not execute carries a failing
``_run_error`` measure, an agent run that ended in an error a failing
``_agent_error`` measure, so either fails the run under the one pass rule
(:func:`omniagents.core.eval.metrics.run_passed`).

:func:`run_suite` expands failure categories, drops unmeasured scenarios
unless asked not to, fans out ``runs_per_prompt`` repeats (``trial``),
runs them concurrently, and aggregates metrics (Wilson intervals,
``slice_by``), a whole-run summary, stats and the run manifest.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import sqlite3
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from omniagents.core.evaluation import (
    build_eval_context,
    final_assistant_text,
    run_measures_async,
)

from .environment import EnvironmentContext, EvalEnvironment, NoOpEnvironment
from .manifest import build_run_manifest
from .measure_tiers import DEFAULT_TIER, measure_names, parse_measures, tag_results
from .metrics import compute_metrics, run_passed, summarize_runs
from .targets import AgentRun, AgentTarget
from .telemetry import build_telemetry, scenario_timer

__all__ = [
    "run_scenario",
    "run_suite",
    "is_synthetic",
    "is_scripted_multi_turn",
    "expand_failure_categories",
]

_logger = logging.getLogger(__name__)

ProgressCallback = Callable[[Dict[str, Any]], None]


def _empty_usage() -> Dict[str, int]:
    return {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def is_synthetic(scenario: Dict[str, Any]) -> bool:
    """``type: synthetic``: a simulated user drives the conversation toward ``goal``."""
    return scenario.get("type") == "synthetic"


def is_scripted_multi_turn(scenario: Dict[str, Any]) -> bool:
    """Whether a scripted scenario runs more than one turn.

    True for ``prompts: [...]`` and for any of the loop knobs:
    ``max_continuations``, ``continuation_prompt``, ``until_sentinel``,
    ``until_sql``.
    """
    if isinstance(scenario.get("prompts"), list):
        return True
    return bool(
        scenario.get("max_continuations")
        or scenario.get("continuation_prompt")
        or scenario.get("until_sentinel")
        or scenario.get("until_sql")
    )


def _scenario_name(scenario: Dict[str, Any]) -> str:
    return scenario.get("name") or scenario.get("id") or "scenario"


# ---------------------------------------------------------------------------
# Tracing
# ---------------------------------------------------------------------------


def _evaluation_metadata(
    scenario: Dict[str, Any],
    scenario_type: str,
    *,
    run_id: Optional[str],
    agent_yaml: Optional[Path],
    catalog: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """What one evaluation run was: its scenario, the synthetic selection it
    exercised and the outcome it expected. Recorded on the run's trace and
    on its conversation, so either is self-contained for review."""
    import hashlib
    import json

    try:
        import omniagents as _oa

        version = getattr(_oa, "__version__", None)
    except Exception:
        version = None
    try:
        blob = json.dumps(catalog, sort_keys=True, ensure_ascii=False)
        catalog_id: Optional[str] = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]
    except Exception:
        catalog_id = None
    scenario_meta = {"id": scenario.get("id"), "name": scenario.get("name")}
    if scenario.get("_source_path"):
        # The file the scenario is written in, so a review can label it.
        scenario_meta["path"] = scenario["_source_path"]
    if scenario.get("goal"):
        scenario_meta["goal"] = scenario.get("goal")
    expected = scenario.get("expect")
    return {
        "run_type": "evaluation",
        "scenario_type": scenario_type,
        "scenario": scenario_meta,
        "run": {"id": run_id},
        **({"expected": expected} if expected else {}),
        "synthetic": {
            "catalog_id": catalog_id,
            "catalog": {"dimensions": catalog},
            "selection": {"mapping": scenario.get("dimensions", {})},
        },
        "config": {"agent_yaml": str(agent_yaml) if agent_yaml else None},
        **({"omniagents_version": version} if version else {}),
    }


class _Trace:
    """Trace ids and the studio trace scope for one run (no-op without a
    workflow), and the run's evaluation metadata either way."""

    def __init__(
        self,
        scenario: Dict[str, Any],
        scenario_type: str,
        *,
        workflow_name: Optional[str],
        studio_trace: Optional[Any],
        agent_yaml: Optional[Path],
        catalog: List[Dict[str, Any]],
    ) -> None:
        self.workflow_name = workflow_name
        self.group_id: Optional[str] = None
        self.agent_run_config: Any = None
        self.user_run_config: Any = None
        self._scope: Any = nullcontext()
        if workflow_name:
            from agents import RunConfig

            sid = scenario.get("id") or scenario.get("name") or "scenario"
            self.group_id = f"{sid}-{uuid.uuid4().hex[:8]}"
            self.agent_run_config = RunConfig(
                workflow_name=workflow_name, group_id=self.group_id
            )
            self.user_run_config = RunConfig(
                workflow_name=f"{workflow_name}_simulated_user",
                group_id=f"{sid}-user-{uuid.uuid4().hex[:8]}",
            )
        self.metadata = _evaluation_metadata(
            scenario,
            scenario_type,
            run_id=self.group_id,
            agent_yaml=agent_yaml,
            catalog=catalog,
        )
        if workflow_name and studio_trace is not None:
            self._scope = studio_trace(
                workflow_name,
                group_id=self.group_id,
                metadata={"omniagents": self.metadata},
            )

    def scope(self) -> Any:
        return self._scope


def _dimension_catalog(eval_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    from .pipeline import _catalog_with_labels

    try:
        dims = (eval_cfg.get("synthetic_data_generation") or {}).get("dimensions")
        return _catalog_with_labels(list(dims or []))
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Conversation drivers
# ---------------------------------------------------------------------------


class _Usage:
    def __init__(self) -> None:
        self.totals = _empty_usage()

    def add(self, telemetry: Dict[str, Any]) -> None:
        usage = telemetry.get("usage") or {}
        for key in self.totals:
            self.totals[key] += int(usage.get(key, 0) or 0)


async def _drive_single(
    run: AgentRun, scenario: Dict[str, Any], trace: _Trace, usage: _Usage
) -> Dict[str, Any]:
    usage.add(await run.run_turn(scenario["prompt"], trace.agent_run_config))
    return {"exchanges": 1}


async def _drive_scripted(
    run: AgentRun,
    scenario: Dict[str, Any],
    context: Optional[EnvironmentContext],
    trace: _Trace,
    usage: _Usage,
) -> Dict[str, Any]:
    if isinstance(scenario.get("prompts"), list):
        prompts: List[str] = list(scenario["prompts"])
    elif "prompt" in scenario:
        prompts = [scenario["prompt"]]
    else:
        raise ValueError(
            "scripted multi-turn scenario requires 'prompts' (list) or 'prompt' (string)"
        )
    if not prompts:
        raise ValueError("scripted multi-turn scenario has empty prompt list")
    max_continuations = max(
        int(scenario.get("max_continuations") or len(prompts)), len(prompts)
    )
    continuation_prompt = scenario.get("continuation_prompt")
    until_sentinel = scenario.get("until_sentinel")
    until_sql = scenario.get("until_sql")
    # ``until_sql`` reads the SQLite file the environment staged
    # (``db_path``); any row returned fires the predicate.
    db_path = (context.data.get("db_path") if context else None) if until_sql else None
    if until_sql and not db_path:
        _logger.warning(
            "scenario %r has until_sql but environment did not stage db_path; "
            "predicate will never fire",
            scenario.get("name"),
        )

    def _sql_hit() -> bool:
        if not until_sql or not db_path:
            return False
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
            try:
                return conn.execute(until_sql).fetchone() is not None
            finally:
                conn.close()
        except sqlite3.Error as exc:
            _logger.warning(
                "until_sql query failed for scenario %r: %s", scenario.get("name"), exc
            )
            return False

    break_reason = "max_continuations"
    turns = 0
    for idx in range(max_continuations):
        prompt = (
            prompts[idx] if idx < len(prompts) else (continuation_prompt or prompts[-1])
        )
        usage.add(await run.run_turn(prompt, trace.agent_run_config))
        turns = idx + 1
        if until_sentinel and until_sentinel in (
            final_assistant_text(run.history()) or ""
        ):
            break_reason = "sentinel"
            break
        if _sql_hit():
            break_reason = "sql_predicate"
            break
    return {
        "exchanges": turns,
        "break_reason": break_reason,
        "turns_completed": turns,
        "max_continuations": max_continuations,
    }


async def _drive_synthetic(
    run: AgentRun,
    scenario: Dict[str, Any],
    defaults: Dict[str, Any],
    base_dir: Path,
    trace: _Trace,
    usage: _Usage,
) -> Dict[str, Any]:
    from .simulated_user import SimulatedUser, UserPersona

    max_exchanges = int(
        scenario.get("max_exchanges", defaults.get("max_exchanges", 10)) or 10
    )
    user = SimulatedUser(
        persona=UserPersona.from_scenario(scenario, base_dir),
        agent_description=run.description,
        model=scenario.get("model", "gpt-4.1"),
        run_config=trace.user_run_config,
    )
    message = await user.get_initial_message()
    for _ in range(max_exchanges):
        usage.add(await run.run_turn(message, trace.agent_run_config))
        if user.completed:
            break
        reply = final_assistant_text(run.history())
        if not reply:
            break
        message = await user.respond(reply)
        if user.completed:
            break
    return {
        "user_completed": user.completed,
        "user_completion_reason": user.completion_reason,
        "max_exchanges": max_exchanges,
    }


# ---------------------------------------------------------------------------
# One scenario run
# ---------------------------------------------------------------------------


def _inputs(scenario: Dict[str, Any], scenario_type: str) -> Dict[str, Any]:
    if scenario_type == "synthetic":
        return {"goal": scenario.get("goal")}
    if isinstance(scenario.get("prompts"), list):
        return {"prompts": list(scenario["prompts"])}
    return {"prompt": scenario.get("prompt", "")}


def _error_measure(name: str, reason: str, details: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": name,
        "passed": False,
        "reason": reason,
        "details": details,
        "tier": "outcome",
    }


def _failed_record(
    base: Dict[str, Any], kind: str, exc: BaseException
) -> Dict[str, Any]:
    message = str(exc) or type(exc).__name__
    return {
        **base,
        "session_id": None,
        "trace_group_id": None,
        "final_assistant_text": None,
        "answered": False,
        "measures": [
            _error_measure(
                "_run_error", f"{kind}: {message}", {"type": kind, "message": message}
            )
        ],
        "passed": False,
        "history_len": 0,
        "exchanges": 0,
        "metadata": {},
        "transcript_path": None,
        "duration_seconds": 0.0,
        "tool_call_count": 0,
        "usage": _empty_usage(),
        "error_count": 0,
        "error": {"type": kind, "message": message},
    }


async def run_scenario(
    target: AgentTarget,
    scenario: Dict[str, Any],
    *,
    environment: Optional[EvalEnvironment] = None,
    defaults: Optional[Dict[str, Any]] = None,
    eval_cfg: Optional[Dict[str, Any]] = None,
    judge_base_dir: Optional[Path] = None,
    workflow_name: Optional[str] = None,
    studio_trace: Optional[Any] = None,
    include_history: bool = False,
) -> Dict[str, Any]:
    """Run ``scenario`` once against ``target`` and return its run record."""
    from .pipeline import run_judges

    defaults = defaults or {}
    eval_cfg = eval_cfg or {}
    env = environment or NoOpEnvironment()
    scenario_type = "synthetic" if is_synthetic(scenario) else "scripted"
    base = {
        "scenario": scenario.get("name", "scenario"),
        "type": scenario_type,
        **_inputs(scenario, scenario_type),
    }

    try:
        context: Optional[EnvironmentContext] = await env.setup(scenario)
    except Exception as exc:
        _logger.debug("Environment setup failed: %s", exc)
        return _failed_record(base, "setup_failed", exc)

    saved_env: Dict[str, Optional[str]] = {}
    run: Optional[AgentRun] = None
    record: Dict[str, Any]
    try:
        # Env vars go in before the agent is built: spec loading reads them.
        if context:
            for key, value in env.get_env_vars(context).items():
                saved_env[key] = os.environ.get(key)
                os.environ[key] = str(value)
        run = await target.start(
            scenario, defaults=defaults, environment=env, context=context
        )
        trace = _Trace(
            scenario,
            (
                "synthetic"
                if scenario_type == "synthetic"
                else (
                    "scripted_multi_turn"
                    if is_scripted_multi_turn(scenario)
                    else "scripted_single_turn"
                )
            ),
            workflow_name=workflow_name,
            studio_trace=studio_trace,
            agent_yaml=target.agent_yaml,
            catalog=_dimension_catalog(eval_cfg),
        )
        usage = _Usage()
        base_dir = Path(judge_base_dir) if judge_base_dir else target.base_dir
        with scenario_timer() as elapsed_fn:
            with trace.scope():
                if scenario_type == "synthetic":
                    drive = await _drive_synthetic(
                        run, scenario, defaults, base_dir, trace, usage
                    )
                elif is_scripted_multi_turn(scenario):
                    drive = await _drive_scripted(run, scenario, context, trace, usage)
                else:
                    drive = await _drive_single(run, scenario, trace, usage)
        history = run.history()
        # The run's conversation carries what it was, like its trace does,
        # so it can be found and reviewed from the conversation store.
        try:
            run.annotate({"omniagents": trace.metadata})
        except Exception as exc:
            _logger.debug("Could not annotate the run's conversation: %s", exc)
        if scenario_type == "synthetic":
            drive["exchanges"] = sum(1 for m in history if m.get("role") == "user")

        # Judges first, so measures can read them (ctx.judge_result(key)).
        judge_metadata = await run_judges(eval_cfg, scenario, history, base_dir)
        environment_data = context.data if context else {}
        run_error = run.run_error()
        ctx_metadata: Dict[str, Any] = {
            "environment_context": environment_data,
            "scenario": scenario,
            **judge_metadata,
        }
        result_metadata: Dict[str, Any] = {
            "environment_context": environment_data,
            **judge_metadata,
        }
        if scenario_type == "synthetic":
            ctx_metadata["user_completed"] = drive["user_completed"]
            ctx_metadata["user_completion_reason"] = drive["user_completion_reason"]
        for key in ("break_reason", "turns_completed", "max_continuations"):
            if key in drive:
                result_metadata[key] = drive[key]
        if trace.group_id:
            result_metadata["group_id"] = trace.group_id
        if run_error is not None:
            ctx_metadata["agent_error"] = run_error
            result_metadata["agent_error"] = run_error

        ctx = build_eval_context(
            history=history, tools=run.available_tools, metadata=ctx_metadata
        )
        ctx._environment = env
        ctx._environment_context = context
        names, tier_by_name = parse_measures(scenario.get("measures"))
        measures_out = await run_measures_async(ctx, names) if names else []
        tag_results(measures_out, tier_by_name)
        if run_error is not None:
            err = run_error.get("error")
            if isinstance(err, dict):
                reason = err.get("message") or err.get("type") or "agent_run_failed"
                details = err
            else:
                reason, details = str(err), {"message": str(err)}
            measures_out.append(_error_measure("_agent_error", reason, details))

        telemetry = build_telemetry(
            elapsed_fn=elapsed_fn,
            history=history,
            stream_result={"usage": usage.totals},
        )
        transcript_path = None
        if target.transcript_root is not None:
            from .transcripts import export_transcript

            transcript_path = export_transcript(
                history,
                scenario,
                project_root=target.transcript_root,
                session_id=run.session_id,
            )
        final_text = final_assistant_text(history)
        record = {
            **base,
            "session_id": run.session_id,
            "trace_group_id": trace.group_id,
            "final_assistant_text": final_text,
            # No final answer is a different failure from a wrong one; it is
            # recorded so aggregation can report it apart.
            "answered": bool((final_text or "").strip()),
            "measures": measures_out,
            "history_len": len(history),
            "exchanges": drive["exchanges"],
            "metadata": result_metadata,
            "transcript_path": transcript_path,
            **telemetry,
        }
        if "break_reason" in drive:
            record["break_reason"] = drive["break_reason"]
        if scenario_type == "synthetic":
            record["user_completed"] = drive["user_completed"]
            record["user_completion_reason"] = drive["user_completion_reason"]
        if include_history:
            record["history"] = history
        record["passed"] = run_passed(record)
    except Exception as exc:
        _logger.debug("Scenario failed: %s", exc)
        record = _failed_record(base, "run_failed", exc)
    finally:
        for key, original in saved_env.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original
        if run is not None:
            try:
                await run.cleanup()
            except Exception as exc:
                _logger.debug("Agent cleanup failed: %s", exc)
        if context:
            try:
                await env.teardown(context)
            except Exception as exc:
                _logger.warning("Environment teardown failed: %s", exc)
    return record


# ---------------------------------------------------------------------------
# A suite of scenarios
# ---------------------------------------------------------------------------


def expand_failure_categories(eval_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The config's scenarios, each with its failure categories' measures merged in.

    Returns copies; the config is not modified. A tier-keyed ``measures``
    dict keeps its shape (added measures go under the default tier).
    """
    from .pipeline import _failure_category_map

    fc_map = _failure_category_map(eval_cfg)
    out: List[Dict[str, Any]] = []
    for raw_sc in eval_cfg.get("scenarios") or []:
        sc = copy.deepcopy(raw_sc)
        raw = sc.get("measures")
        flat, tier_by_name = parse_measures(raw)
        for fc in sc.get("failure_categories") or []:
            fc_name = (
                fc
                if isinstance(fc, str)
                else (
                    (fc.get("name") or fc.get("key") or fc.get("id"))
                    if isinstance(fc, dict)
                    else None
                )
            )
            for m in fc_map.get(str(fc_name), []) if fc_name else []:
                if str(m) not in tier_by_name:
                    flat.append(str(m))
                    tier_by_name[str(m)] = DEFAULT_TIER
        if flat:
            if isinstance(raw, dict):
                rebuilt: Dict[str, List[str]] = {}
                for n in flat:
                    rebuilt.setdefault(tier_by_name.get(n, DEFAULT_TIER), []).append(n)
                sc["measures"] = rebuilt
            else:
                sc["measures"] = flat
        out.append(sc)
    return out


def _studio_trace_for(workflow_name: Optional[str]) -> Optional[Any]:
    if not workflow_name:
        return None
    try:
        from omniagents.core.tracing.local import trace as studio_trace

        return studio_trace
    except ImportError:
        return None


async def run_suite(
    target: AgentTarget,
    eval_cfg: Dict[str, Any],
    *,
    eval_dir: Optional[Path] = None,
    project_root: Optional[Path] = None,
    environment: Optional[EvalEnvironment] = None,
    include_unmeasured: bool = False,
    progress_cb: Optional[ProgressCallback] = None,
    concurrency: int = 5,
    workflow_name: Optional[str] = None,
    include_history: bool = False,
    selection: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run every scenario in ``eval_cfg`` against ``target``.

    ``eval_cfg`` is a merged evaluation config (``EvalConfig.config``):
    ``scenarios``, ``judges``, ``metrics``, ``defaults``, ``environment``,
    ``failure_categories``. Measures must already be registered.
    ``selection`` (``select``/``exclude``/``split``/``holdout``) only
    describes, for the manifest, how the scenarios were chosen.

    Returns ``{"runs", "metrics", "summary", "stats", "manifest"}`` (plus
    ``environment_error``/``warnings`` when a declared environment failed
    to build).
    """
    from omniagents.core.evaluation.registry import _MEASURES

    defaults = eval_cfg.get("defaults") or {}
    default_runs = max(1, int(defaults.get("runs_per_prompt", 1) or 1))
    all_scenarios = expand_failure_categories(eval_cfg)
    scenarios = (
        all_scenarios
        if include_unmeasured
        else [sc for sc in all_scenarios if measure_names(sc.get("measures"))]
    )

    warnings: List[str] = []
    env_error: Optional[str] = None
    env_cfg = eval_cfg.get("environment")
    if environment is None and env_cfg:
        from .environment_config import build_environment

        try:
            environment = build_environment(
                env_cfg,
                project_root=project_root or target.base_dir,
                eval_dir=eval_dir,
            )
        except Exception as exc:
            env_error = f"Failed to build declared environment: {exc}"
            warnings.append(f"Environment loading failed: {env_error}")
            _logger.error(env_error)

    discovered = sorted(_MEASURES.keys())
    unknown_counts: Dict[str, int] = {}
    unknown_scenarios = 0
    for sc in scenarios:
        unknown = [m for m in measure_names(sc.get("measures")) if m not in _MEASURES]
        if unknown:
            unknown_scenarios += 1
            for m in unknown:
                unknown_counts[m] = unknown_counts.get(m, 0) + 1

    plan: List[Tuple[Dict[str, Any], int, int]] = []
    for sc in scenarios:
        repeats = max(1, int(sc.get("runs_per_prompt") or default_runs))
        plan.extend((sc, trial, repeats) for trial in range(repeats))
    total = len(plan)
    if progress_cb:
        try:
            progress_cb({"event": "start", "total": total})
        except Exception:
            pass

    studio_trace = _studio_trace_for(workflow_name)
    judge_base_dir = Path(eval_dir) if eval_dir else None
    sem = asyncio.Semaphore(max(1, int(concurrency)))
    completed = 0

    async def _one(sc: Dict[str, Any], trial: int, repeats: int) -> Dict[str, Any]:
        nonlocal completed
        async with sem:
            record = await run_scenario(
                target,
                sc,
                environment=environment,
                defaults=defaults,
                eval_cfg=eval_cfg,
                judge_base_dir=judge_base_dir,
                workflow_name=workflow_name,
                studio_trace=studio_trace,
                include_history=include_history,
            )
        record["scenario_config"] = sc
        # Which repeat of the scenario this is (0-based), and how many.
        record["trial"] = trial
        record["repeats"] = repeats
        completed += 1
        if progress_cb:
            measures = record.get("measures") or []
            try:
                progress_cb(
                    {
                        "event": "progress",
                        "index": completed,
                        "total": total,
                        "scenario": record.get("scenario"),
                        "scenario_id": sc.get("id"),
                        "passed": sum(1 for m in measures if m.get("passed") is True),
                        "failed": sum(1 for m in measures if m.get("passed") is False),
                        "skipped": not measures,
                    }
                )
            except Exception:
                pass
        return record

    runs = list(await asyncio.gather(*(_one(sc, t, n) for sc, t, n in plan)))

    metrics = compute_metrics(
        runs, eval_cfg.get("metrics") or [], default_slice_by=defaults.get("slice_by")
    )
    selection = selection or {}
    out: Dict[str, Any] = {
        "agent_yaml": str(target.agent_yaml) if target.agent_yaml else None,
        "runs": runs,
        "metrics": metrics,
        "summary": summarize_runs(runs),
        "stats": {
            "repeats": default_runs,
            "answered": sum(1 for r in runs if r.get("answered")),
            "total_scenarios": len(all_scenarios),
            "included_scenarios": len(scenarios),
            "skipped_unmeasured": len(all_scenarios) - len(scenarios),
            "synthetic_scenarios": sum(1 for sc in scenarios if is_synthetic(sc)),
            "scripted_multi_turn_scenarios": sum(
                1
                for sc in scenarios
                if not is_synthetic(sc) and is_scripted_multi_turn(sc)
            ),
            "measures_discovered_count": len(discovered),
            "measures_discovered": discovered,
            "unknown_measures": unknown_counts,
            "unknown_scenarios": unknown_scenarios,
        },
        "manifest": build_run_manifest(
            target.describe(),
            defaults=defaults,
            select=selection.get("select"),
            exclude=selection.get("exclude"),
            split=selection.get("split"),
            holdout=selection.get("holdout"),
            repeats=default_runs,
            scenarios=len(all_scenarios),
        ),
    }
    if env_error:
        out["environment_error"] = env_error
        out["warnings"] = warnings
    return out
