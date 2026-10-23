from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
import yaml
from omniagents.core.eval.stratified_sampler import StratifiedBatchSampler


def _optimization_dir(
    eval_config: Any, kind: str, subject: str, output_dir: str | None, resume: bool
) -> Path:
    """Where an optimization writes: a new ``optimizations/<id>_<kind>_<subject>/``,
    or ``output_dir``. ``resume`` continues the optimization in
    ``output_dir``, which must exist."""
    from omniagents.core.eval.layout import EvalLayout
    from omniagents.core.eval.results import generate_run_id

    if output_dir:
        run_dir = Path(output_dir)
        if not run_dir.is_absolute():
            run_dir = (eval_config.project_root / run_dir).resolve()
    elif resume:
        raise SystemExit("Name the optimization to resume with --output <its folder>")
    else:
        layout = EvalLayout(eval_config.eval_dir, eval_config.project_root)
        run_dir = layout.optimizations_dir / f"{generate_run_id()}_{kind}_{subject}"
    if resume and not run_dir.is_dir():
        raise SystemExit(f"No optimization to resume at {run_dir}")
    if not resume and run_dir.is_dir() and any(run_dir.iterdir()):
        raise SystemExit(
            f"{run_dir} holds an optimization; resume it with --resume or name another"
        )
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _dataset_split(eval_config: Any, category: str, which: str) -> Path:
    from omniagents.core.eval.layout import EvalLayout

    layout = EvalLayout(eval_config.eval_dir, eval_config.project_root)
    return layout.judge_dataset_dir(category) / f"{which}.yml"


def optimize_agent_from_project(
    proj_path: str,
    agent_key: str | None,
    category: str | None,
    train_file: str | None,
    dev_file: str | None,
    test_file: str | None,
    positives_only: bool,
    max_turns: int | None,
    max_metric_calls: int,
    reflection_model: str,
    score_mode: str,
    optimizer_key: str | None,
    seed: int,
    output_dir: str | None,
    resume: bool,
    apply: bool,
    log_hook: Optional[Callable[[str], None]] = None,
    train_select: str | None = None,
    dev_select: str | None = None,
    test_select: str | None = None,
) -> Dict[str, Any]:
    try:
        import gepa  # type: ignore
    except Exception:
        raise SystemExit(
            "This command requires the 'gepa' package. Install with: pip install gepa[full]"
        )
    from omniagents.core.eval.gepa_agent_adapter import OmniAgentsAgentAdapter
    from omniagents.core.eval.loader import load_eval_config, filter_scenarios
    from omniagents.core.project.agent_files import update_agent

    _eval_config = load_eval_config(
        str(proj_path),
        select=None,
        exclude=None,
        agent_key=(str(agent_key or "") or None),
    )
    compiled_cfg_path = _eval_config.agent_path
    _P = Path
    base = _P(proj_path).resolve()
    if base.is_file():
        base = base.parent
    cat = (category or "").strip()

    # Selector-based split resolution (evaluations/-native flow). When any of
    # --train-select / --dev-select / --test-select is provided, the
    # scenarios for that role come from the project's scenarios graph filtered
    # by the selector — no external split files needed. Per-role selectors
    # short-circuit per-role file resolution; mixed file/selector usage is
    # supported.
    using_any_selector = bool(
        (train_select or "").strip()
        or (dev_select or "").strip()
        or (test_select or "").strip()
    )

    def _filter_by_selector(selector: str | None) -> Optional[List[Dict[str, Any]]]:
        if not selector or not str(selector).strip():
            return None
        all_scenarios = list(_eval_config.config.get("scenarios") or [])
        return list(filter_scenarios(all_scenarios, str(selector), None))

    train_via_select = _filter_by_selector(train_select)
    dev_via_select = _filter_by_selector(dev_select)
    test_via_select = _filter_by_selector(test_select)

    def _resolve_split(which, explicit):
        if explicit:
            p = _P(explicit)
            return p if p.is_absolute() else (base / p).resolve()
        if not cat:
            return None
        candidate = _dataset_split(_eval_config, cat, which)
        return candidate if candidate.exists() else None

    # Resolve file paths only for roles not covered by a selector.
    tr_path = (
        None
        if train_via_select is not None
        else (
            _resolve_split("train", train_file)
            if (cat or not train_file)
            else (
                _P(train_file)
                if _P(train_file).is_absolute()
                else (base / train_file).resolve()
            )
        )
    )
    dv_path = (
        None
        if dev_via_select is not None
        else (
            _resolve_split("dev", dev_file)
            if (cat or not dev_file)
            else (
                _P(dev_file)
                if _P(dev_file).is_absolute()
                else (base / dev_file).resolve()
            )
        )
    )
    ts_path = (
        None
        if test_via_select is not None
        else (
            _resolve_split("test", test_file)
            if (cat or not test_file)
            else (
                _P(test_file)
                if _P(test_file).is_absolute()
                else (base / test_file).resolve()
            )
        )
    )

    # Only raise the "missing splits" error when NO selector was used at all
    # AND no file path resolved successfully.
    if not using_any_selector and not any([tr_path, dv_path, ts_path]):
        if cat:
            raise SystemExit(
                f"No judge dataset for category '{cat}' at "
                f"{_dataset_split(_eval_config, cat, 'dev').parent}. Build one with "
                "`omniagents judge dataset`, or pass split files or selectors."
            )
        raise SystemExit(
            "Could not locate scenario splits. Provide --category, explicit split files, or --train-select/--dev-select/--test-select."
        )

    def _read_scenarios(p: _P) -> List[Dict[str, Any]]:
        y = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        items = [x for x in (y.get("scenarios") or []) if isinstance(x, dict)]
        return items

    def _resolve_role(via_select, path):
        if via_select is not None:
            # Scenarios sourced from the project graph default to label=True
            # so the stratified sampler treats them as positives. Explicit
            # `label: false` on the source scenario is preserved.
            out: List[Dict[str, Any]] = []
            for sc in via_select:
                sc2 = dict(sc)
                if "label" not in sc2:
                    sc2["label"] = True
                out.append(sc2)
            return out
        return _read_scenarios(path) if path and path.exists() else []

    train_raw = _resolve_role(train_via_select, tr_path)
    dev_raw = _resolve_role(dev_via_select, dv_path)
    test_raw = _resolve_role(test_via_select, ts_path)
    if not cat:
        try:
            for it in dev_raw or train_raw or test_raw:
                c = it.get("target_category")
                if c:
                    cat = str(c)
                    break
        except Exception:
            pass
    if not cat:
        raise SystemExit(
            "Could not determine failure category from splits; please provide --category."
        )
    try:
        from omniagents.core.eval.pipeline import _failure_category_map as _fc_map_guard

        # Read failure_categories from the merged eval config (which already
        # incorporated evaluations/evaluation.yml during load_eval_config),
        # not from the agent YAML directly. The agent YAML never declares
        # `evaluation` in this scaffold.
        ev_guard = _eval_config.config or {}
        measures_for_cat = list((_fc_map_guard(ev_guard) or {}).get(cat) or [])
        if not measures_for_cat:
            raise SystemExit(
                f"Failure category '{cat}' has no measures configured in evaluations/evaluation.yml. Add a 'failure_categories' block under 'evaluation:' that maps '{cat}' to one or more measure names."
            )
    except SystemExit:
        raise
    except Exception:
        pass

    def _maybe_filter(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if bool(positives_only):
            return [x for x in items if bool(x.get("label")) is True]
        return items

    trainset = _maybe_filter(train_raw)
    devset = _maybe_filter(dev_raw)
    testset = _maybe_filter(test_raw)
    try:
        thr = 30

        def _warn_split(_name, _ds):
            p_cnt = sum(1 for x in (_ds or []) if bool(x.get("label")) is True)
            if p_cnt < thr:
                print(
                    f"Warning: split '{_name}' has only {p_cnt} positive cases (<{thr})."
                )

        _warn_split("train", trainset)
        _warn_split("dev", devset)
        _warn_split("test", testset)
    except Exception:
        pass
    cfg = yaml.safe_load(_P(compiled_cfg_path).read_text(encoding="utf-8")) or {}

    def _read_text_file(base_dir: _P, rel: str) -> str:
        p = _P(rel)
        if not p.is_absolute():
            p = (base_dir / p).resolve()
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            return ""

    seed_instructions = cfg.get("instructions") or ""
    if (not seed_instructions) and cfg.get("instructions_file"):
        seed_instructions = _read_text_file(
            _P(compiled_cfg_path).resolve().parent, str(cfg.get("instructions_file"))
        )
    if not seed_instructions:
        seed_instructions = "You are a helpful assistant."
    seed_candidate = {"instructions": seed_instructions}
    workflow_name = None
    try:
        tracing = _eval_config.tracing_config or {}
        if str(tracing.get("provider", "")).lower() == "studio":
            base_name = tracing.get("name") or _P(compiled_cfg_path).stem
            ts = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
            workflow_name = f"{base_name}_studio_optimize_{cat}_{ts}"
    except Exception:
        workflow_name = None
    adapter = OmniAgentsAgentAdapter(
        _P(compiled_cfg_path),
        cat,
        agent_max_turns=(int(max_turns) if max_turns is not None else None),
        score_mode=str(score_mode or "fraction"),
        optimizer_key=(str(optimizer_key) if optimizer_key else None),
        workflow_name=workflow_name,
        eval_config=_eval_config,
    )
    run_dir = _optimization_dir(_eval_config, "agent", cat, output_dir, resume)
    try:
        cfg_tr = yaml.safe_load(_P(compiled_cfg_path).read_text(encoding="utf-8")) or {}
        tracing = cfg_tr.get("tracing") or {}
        if str(tracing.get("provider", "")).lower() == "studio" and workflow_name:
            from omniagents.core.tracing import local as tracing_module

            project = tracing.get("project") or "default"
            resume_tr = (tracing.get("settings") or {}).get("resume", "never")
            tracing_module.init(
                project=str(project),
                name=workflow_name,
                resume=str(resume_tr),
                replace_default=True,
            )
    except Exception:
        pass
    try:
        from gepa.core.engine import GEPAEngine  # type: ignore
        from gepa.core.result import GEPAResult  # type: ignore
        from gepa.core.data_loader import ensure_loader  # type: ignore
        from gepa.logging.experiment_tracker import create_experiment_tracker  # type: ignore
        from gepa.strategies.candidate_selector import ParetoCandidateSelector  # type: ignore
        from gepa.strategies.component_selector import (
            RoundRobinReflectionComponentSelector,
        )  # type: ignore
        from gepa.utils import FileStopper, CompositeStopper, MaxMetricCallsStopper  # type: ignore
        from gepa.proposer.reflective_mutation.reflective_mutation import (
            ReflectiveMutationProposer,
        )  # type: ignore
    except Exception as e:
        raise SystemExit(f"Could not import GEPA components: {e}")

    import random as _random

    _seed = int(seed or 0)
    _rng = _random.Random(_seed)
    candidate_selector = ParetoCandidateSelector(rng=_rng)
    module_selector = RoundRobinReflectionComponentSelector()
    minibatch_size = 3
    batch_sampler = StratifiedBatchSampler(
        trainset=trainset,
        minibatch_size=minibatch_size,
        label_getter="label",
        pos_fraction=0.5,
        min_pos_per_batch=1,
        min_neg_per_batch=1,
        with_replacement=True,
        seed=_seed,
        rng=_rng,
    )
    experiment_tracker = create_experiment_tracker(
        use_wandb=False,
        wandb_api_key=None,
        wandb_init_kwargs=None,
        use_mlflow=False,
        mlflow_tracking_uri=None,
        mlflow_experiment_name=None,
    )

    class _RunLogger:
        def __init__(self, path: Path, hook: Optional[Callable[[str], None]]):
            self.path = path
            self.hook = hook
            self._lock = threading.Lock()

        def log(self, message: str) -> None:
            text = str(message)
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(text + "\n")
            print(text)
            if self.hook:
                try:
                    self.hook(text)
                except Exception:
                    pass

    log_path = run_dir / "gepa.log"
    if log_hook:
        log_hook(f"LOG_PATH:{log_path}")
    logger = _RunLogger(log_path, log_hook)
    stoppers = []
    try:
        stoppers.append(FileStopper(str(run_dir / "gepa.stop")))
    except Exception:
        pass
    try:
        stoppers.append(MaxMetricCallsStopper(int(max_metric_calls or 100)))
    except Exception:
        pass
    base_stop_callback = None
    if len(stoppers) == 1:
        base_stop_callback = stoppers[0]
    elif len(stoppers) > 1:
        base_stop_callback = CompositeStopper(*stoppers)

    start_time = time.perf_counter()
    max_calls = int(max_metric_calls or 100)

    if log_hook:

        def _progress_stop(state):  # type: ignore
            try:
                progress = state.total_num_evals
                elapsed = time.perf_counter() - start_time
                eta_seconds = ""
                if progress > 0 and elapsed > 0:
                    rate = progress / elapsed
                    remaining = max(0, max_calls - progress)
                    if rate > 0:
                        eta_seconds = f"{remaining / rate:.2f}"
                log_hook(f"PROGRESS:{progress}:{eta_seconds}")
            except Exception:
                pass
            if base_stop_callback is not None:
                return base_stop_callback(state)
            return False

        stop_callback = _progress_stop
    else:
        stop_callback = base_stop_callback
    # gepa >= 0.1.x expects the trainset wrapped as a DataLoader so the
    # proposer can call .fetch(ids) and len() on it.
    trainset_loader = ensure_loader(trainset)
    reflective_proposer = ReflectiveMutationProposer(
        logger=logger,
        trainset=trainset_loader,
        adapter=adapter,
        candidate_selector=candidate_selector,
        module_selector=module_selector,
        batch_sampler=batch_sampler,
        perfect_score=1,
        skip_perfect_score=True,
        experiment_tracker=experiment_tracker,
        reflection_lm=str(reflection_model or "gpt-4.1"),
    )

    try:
        adapter.debug_dir = run_dir / "debug"
    except Exception:
        pass

    # gepa >= 0.1.x takes the adapter directly (and builds its own internal
    # evaluator that calls adapter.evaluate). frontier_type='instance' is the
    # scenario-level Pareto frontier — the closest analogue to the older API
    # that took only (outputs, scores).
    engine = GEPAEngine(
        adapter=adapter,
        run_dir=str(run_dir),
        valset=devset,
        seed_candidate=seed_candidate,
        perfect_score=1,
        seed=_seed,
        reflective_proposer=reflective_proposer,
        merge_proposer=None,
        frontier_type="instance",
        logger=logger,
        experiment_tracker=experiment_tracker,
        track_best_outputs=False,
        display_progress_bar=True,
        raise_on_exception=True,
        stop_callback=stop_callback,
    )

    with experiment_tracker:
        state = engine.run()

    result = GEPAResult.from_state(state)
    if log_hook:
        try:
            log_hook(f"PROGRESS:{state.total_num_evals}:0")
        except Exception:
            pass
    best = result.best_candidate

    def _acc(ds, cand):
        ev = adapter.evaluate(ds, cand, capture_traces=False)
        return (sum(ev.scores) / len(ev.scores)) if ev.scores else 0.0

    dev_acc = _acc(devset, best)
    test_acc = _acc(testset, best)
    out_cfg = {
        "category": cat,
        "optimized": {
            "instructions": best.get("instructions"),
        },
        "metrics": {
            "dev_score": dev_acc,
            "test_score": test_acc,
            "score_mode": str(score_mode or "fraction"),
            "positives_only": bool(positives_only),
        },
    }
    out_json = run_dir / "result.json"
    out_json.write_text(json.dumps(out_cfg, indent=2), encoding="utf-8")
    print(
        f"Optimized agent instructions for category '{cat}'. Dev={dev_acc:.3f} Test={test_acc:.3f}"
    )
    print(f"Wrote: {out_json}")

    class _LiteralStr(str):
        pass

    class _LiteralDumper(yaml.SafeDumper):
        pass

    def _lit_representer(dumper, data):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")

    _LiteralDumper.add_representer(_LiteralStr, _lit_representer)
    instr_val = (
        best.get("instructions") if isinstance(best.get("instructions"), str) else ""
    )
    instr_node = _LiteralStr(instr_val) if ("\n" in instr_val) else instr_val
    override = {"agent": {"instructions": instr_node}}
    out_yml = run_dir / "agent_override.yml"
    out_yml.write_text(
        yaml.dump(override, Dumper=_LiteralDumper, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"Agent override written: {out_yml}")
    best_instructions = (
        best.get("instructions") if isinstance(best.get("instructions"), str) else None
    )
    applied = False
    apply_error = None
    if bool(apply) and best_instructions is not None:
        # The evaluated agent is agents/<key>/agent.yml.
        key = agent_key or _eval_config.agent_path.parent.name
        try:
            update_agent(proj_path, key, instructions=best_instructions)
            applied = True
        except Exception as exc:  # noqa: BLE001 - reported, the run's results stand
            apply_error = str(exc)
            logger.log(f"Could not apply the optimized instructions: {apply_error}")
    return {
        "run_dir": str(run_dir),
        "out_json": str(out_json),
        "dev_score": dev_acc,
        "test_score": test_acc,
        "category": cat,
        "instructions": best_instructions,
        "applied": applied,
        "apply_error": apply_error,
        "log_file": str(log_path),
    }


def optimize_judge_from_project(
    proj_path: str,
    agent_key: str | None,
    category: str | None,
    train_file: str | None,
    dev_file: str | None,
    test_file: str | None,
    judge_key: str | None,
    source: str,
    max_turns: int,
    max_metric_calls: int,
    reflection_model: str | None,
    optimize_components: str,
    optimizer_key: str | None,
    seed: int,
    output_dir: str | None,
    resume: bool,
    apply: bool,
) -> Dict[str, Any]:
    try:
        import gepa  # type: ignore
    except Exception:
        raise SystemExit(
            "This command requires the 'gepa' package. Install with: pip install gepa[full]"
        )
    from omniagents.core.eval.gepa_judge_adapter import OmniAgentsJudgeAdapter
    from omniagents.core.eval.stratified_sampler import StratifiedBatchSampler
    from omniagents.core.eval.loader import load_eval_config
    from omniagents.core.eval.workspace import EvalWorkspace

    _eval_config = load_eval_config(
        str(proj_path),
        select=None,
        exclude=None,
        agent_key=(str(agent_key or "") or None),
    )
    compiled_cfg_path = _eval_config.agent_path
    _P = Path
    base = _P(proj_path).resolve()
    if base.is_file():
        base = base.parent
    cat = (category or "").strip()

    def _resolve_split(which, explicit):
        if explicit:
            p = _P(explicit)
            return p if p.is_absolute() else (base / p).resolve()
        if not cat:
            raise SystemExit("Provide --category or explicit split files.")
        candidate = _dataset_split(_eval_config, cat, which)
        if candidate.exists():
            return candidate
        raise SystemExit(
            f"No judge dataset split for category '{cat}' ({which}) at {candidate}. "
            "Build one with `omniagents judge dataset`."
        )

    tr_path = (
        _resolve_split("train", train_file)
        if cat or not train_file
        else (
            _P(train_file)
            if _P(train_file).is_absolute()
            else (base / train_file).resolve()
        )
    )
    dv_path = (
        _resolve_split("dev", dev_file)
        if cat or not dev_file
        else (
            _P(dev_file) if _P(dev_file).is_absolute() else (base / dev_file).resolve()
        )
    )
    ts_path = (
        _resolve_split("test", test_file)
        if cat or not test_file
        else (
            _P(test_file)
            if _P(test_file).is_absolute()
            else (base / test_file).resolve()
        )
    )

    def _read_items(p: _P):
        y = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        items = [x for x in (y.get("scenarios") or []) if isinstance(x, dict)]
        return [
            {
                "id": str(x.get("id")),
                "label": bool(x.get("label")),
                "name": x.get("name"),
            }
            for x in items
        ]

    trainset = _read_items(tr_path)
    devset = _read_items(dv_path)
    testset = _read_items(ts_path)
    try:
        thr = 30

        def _warn_split(_name, _ds):
            p_cnt = sum(1 for x in (_ds or []) if bool(x.get("label")) is False)
            f_cnt = sum(1 for x in (_ds or []) if bool(x.get("label")) is True)
            cat_for_warn = cat or dv_path.parent.name
            prefix = f"category '{cat_for_warn}' " if cat_for_warn else ""
            if p_cnt < thr:
                print(
                    f"Warning: {prefix}split '{_name}' has only {p_cnt} Pass labels (<{thr})."
                )
            if f_cnt < thr:
                print(
                    f"Warning: {prefix}split '{_name}' has only {f_cnt} Fail labels (<{thr})."
                )

        _warn_split("train", trainset)
        _warn_split("dev", devset)
        _warn_split("test", testset)
    except Exception:
        pass

    from omniagents.core.eval.reviews import dataset_conversations

    # What the judge reads: each example's answer as it was reviewed.
    conversations = dataset_conversations(
        [
            item
            for path in (tr_path, dv_path, ts_path)
            for item in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(
                "scenarios"
            )
            or []
        ]
    )
    wanted = {
        item["id"] if item.get("id") not in (None, "None") else str(item.get("name"))
        for item in trainset + devset + testset
    }
    missing = sorted(wanted - set(conversations))
    if missing:
        raise SystemExit(
            f"{len(missing)} example(s) in the judge dataset carry no reviewed "
            f"answer (e.g. {missing[0]}); build the judge dataset from reviews"
        )

    jkey = judge_key
    if not jkey:
        try:
            from omniagents.core.eval.pipeline import (
                _failure_category_map as _fcmap,
                _derive_judge_keys_from_measures as _derive_j,
            )

            measures = list((_fcmap(_eval_config.config) or {}).get(cat) or [])
            candidates = _derive_j(measures)
            uniq = sorted(set(candidates))
            if len(uniq) == 1:
                jkey = uniq[0]
            elif len(uniq) == 0:
                raise SystemExit(
                    "Could not infer judge from category. Provide --judge <key>."
                )
            else:
                raise SystemExit(
                    f"Multiple judges map to category '{cat}': {', '.join(uniq)}. Provide --judge <key>."
                )
        except Exception:
            raise SystemExit("Provide --judge <key>.")

    # The judge as the project defines it; the loader resolved its files.
    jcfg = (_eval_config.config.get("judges") or {}).get(jkey)
    if not isinstance(jcfg, dict):
        raise SystemExit(f"Judge not found: {jkey}")
    agent_cfg = jcfg.get("agent") or {}
    input_cfg = jcfg.get("input") or {}
    seed_instructions = agent_cfg.get("instructions") or ""
    if (not seed_instructions) and agent_cfg.get("instructions_file"):
        seed_instructions = _P(agent_cfg["instructions_file"]).read_text(
            encoding="utf-8"
        )
    seed_template = input_cfg.get("template") or ""
    if (not seed_template) and input_cfg.get("template_file"):
        seed_template = _P(input_cfg["template_file"]).read_text(encoding="utf-8")
    if not seed_template:
        seed_template = "{{ text }}"
    if not seed_instructions:
        seed_instructions = "You are an evaluation agent. Return ONLY JSON."
    seed_candidate = {}
    comps = [
        s.strip()
        for s in str(optimize_components or "instructions").split(",")
        if s.strip()
    ]
    if "instructions" in comps:
        seed_candidate["instructions"] = seed_instructions
    if "template" in comps:
        seed_candidate["template"] = seed_template
    workflow_name = None
    try:
        tracing = _eval_config.tracing_config or {}
        if str(tracing.get("provider", "")).lower() == "studio":
            base_name = tracing.get("name") or _P(compiled_cfg_path).stem
            ts = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
            workflow_name = f"{base_name}_judge_optimize_{jkey}_{cat}_{ts}"
    except Exception:
        workflow_name = None
    adapter = OmniAgentsJudgeAdapter(
        _P(compiled_cfg_path),
        jkey,
        conversations,
        category=(str(cat) if cat else None),
        source=str(source or "final_assistant_text"),
        judge_max_turns=int(max_turns or 1),
        optimizer_key=(str(optimizer_key) if optimizer_key else None),
        workflow_name=workflow_name,
        eval_config=_eval_config,
    )
    run_dir = _optimization_dir(_eval_config, "judge", str(jkey), output_dir, resume)
    try:
        adapter.debug_dir = run_dir / "debug"
    except Exception:
        pass
    try:
        cfg_tr = yaml.safe_load(_P(compiled_cfg_path).read_text(encoding="utf-8")) or {}
        tracing = cfg_tr.get("tracing") or {}
        if str(tracing.get("provider", "")).lower() == "studio" and workflow_name:
            from omniagents.core.tracing import local as tracing_module

            project = tracing.get("project") or "default"
            resume_tr = (tracing.get("settings") or {}).get("resume", "never")
            tracing_module.init(
                project=str(project),
                name=workflow_name,
                resume=str(resume_tr),
                replace_default=True,
            )
    except Exception:
        pass
    try:
        from gepa.core.engine import GEPAEngine
        from gepa.core.result import GEPAResult
        from gepa.logging.experiment_tracker import create_experiment_tracker
        from gepa.logging.logger import StdOutLogger
        from gepa.strategies.candidate_selector import ParetoCandidateSelector
        from gepa.strategies.component_selector import (
            RoundRobinReflectionComponentSelector,
        )
        from gepa.utils import FileStopper, CompositeStopper, MaxMetricCallsStopper
    except Exception as e:
        raise SystemExit(f"Could not import GEPA internals for custom sampling: {e}")

    import random as _random

    _seed = int(seed or 0)
    _rng = _random.Random(_seed)
    candidate_selector = ParetoCandidateSelector(rng=_rng)
    module_selector = RoundRobinReflectionComponentSelector()
    minibatch_size = 3
    batch_sampler = StratifiedBatchSampler(
        trainset=trainset,
        minibatch_size=minibatch_size,
        label_getter="label",
        pos_fraction=0.5,
        min_pos_per_batch=1,
        min_neg_per_batch=1,
        with_replacement=True,
        seed=_seed,
        rng=_rng,
    )
    experiment_tracker = create_experiment_tracker(
        use_wandb=False,
        wandb_api_key=None,
        wandb_init_kwargs=None,
        use_mlflow=False,
        mlflow_tracking_uri=None,
        mlflow_experiment_name=None,
    )
    logger = StdOutLogger()
    stoppers = []
    try:
        stoppers.append(FileStopper(str(run_dir / "gepa.stop")))
    except Exception:
        pass
    try:
        stoppers.append(MaxMetricCallsStopper(int(max_metric_calls or 100)))
    except Exception:
        pass
    stop_callback = (
        stoppers[0]
        if len(stoppers) == 1
        else (CompositeStopper(*stoppers) if stoppers else None)
    )
    reflective_proposer = None
    try:
        from gepa.proposer.reflective_mutation.reflective_mutation import (
            ReflectiveMutationProposer,
        )

        reflective_proposer = ReflectiveMutationProposer(
            logger=logger,
            trainset=trainset,
            adapter=adapter,
            candidate_selector=candidate_selector,
            module_selector=module_selector,
            batch_sampler=batch_sampler,
            perfect_score=1,
            skip_perfect_score=True,
            experiment_tracker=experiment_tracker,
            reflection_lm=None,
        )
    except Exception as e:
        raise SystemExit(f"Failed to create reflective proposer: {e}")

    def _evaluator(inputs, prog):
        ev = adapter.evaluate(inputs, prog, capture_traces=False)
        return ev.outputs, ev.scores

    engine = GEPAEngine(
        run_dir=str(run_dir),
        evaluator=_evaluator,
        valset=devset,
        seed_candidate=seed_candidate,
        perfect_score=1,
        seed=_seed,
        reflective_proposer=reflective_proposer,
        merge_proposer=None,
        logger=logger,
        experiment_tracker=experiment_tracker,
        track_best_outputs=False,
        display_progress_bar=True,
        raise_on_exception=True,
        stop_callback=stop_callback,
    )
    with experiment_tracker:
        state = engine.run()
    from gepa.core.result import GEPAResult

    result = GEPAResult.from_state(state)
    best = result.best_candidate

    def _judge_metrics(ds, cand):
        ev = adapter.evaluate(ds, cand, capture_traces=False)
        tp = tn = fp = fn = 0
        total = len(ds)
        for i, item in enumerate(ds):
            try:
                label = bool(item.get("label"))
            except Exception:
                label = False
            pred = None
            try:
                pred = ev.outputs[i].get("pred")
            except Exception:
                pred = None
            if pred is True and label is True:
                tp += 1
            elif pred is False and label is False:
                tn += 1
            elif pred is True and label is False:
                fp += 1
            elif pred is False and label is True:
                fn += 1
        denom_acc = max(1, total)
        acc = (tp + tn) / denom_acc
        tpr = (tp / (tp + fn)) if (tp + fn) > 0 else None
        tnr = (tn / (tn + fp)) if (tn + fp) > 0 else None
        return {
            "accuracy": acc,
            "counts": {"TP": tp, "TN": tn, "FP": fp, "FN": fn},
            "tpr": tpr,
            "tnr": tnr,
        }

    dev_metrics = _judge_metrics(devset, best)
    test_metrics = _judge_metrics(testset, best)
    dev_acc = dev_metrics["accuracy"]
    test_acc = test_metrics["accuracy"]
    out_cfg = {
        "judge": jkey,
        "optimized": {
            "instructions": best.get("instructions"),
            "template": best.get("template"),
        },
        "metrics": {
            "dev_accuracy": dev_acc,
            "dev_counts": dev_metrics.get("counts"),
            "dev_tpr": dev_metrics.get("tpr"),
            "dev_tnr": dev_metrics.get("tnr"),
            "test_accuracy": test_acc,
            "test_counts": test_metrics.get("counts"),
            "test_tpr": test_metrics.get("tpr"),
            "test_tnr": test_metrics.get("tnr"),
        },
    }
    out_json = run_dir / "result.json"
    out_json.write_text(json.dumps(out_cfg, indent=2), encoding="utf-8")
    print(f"Optimized judge '{jkey}'. Dev acc={dev_acc:.3f} Test acc={test_acc:.3f}")

    def _fmt(v):
        return f"{v:.3f}" if isinstance(v, (int, float)) and v is not None else "NA"

    d = dev_metrics
    t = test_metrics
    print(
        f"Dev: TPR={_fmt(d['tpr'])} TNR={_fmt(d['tnr'])} Counts: TP={d['counts']['TP']} TN={d['counts']['TN']} FP={d['counts']['FP']} FN={d['counts']['FN']}"
    )
    print(
        f"Test: TPR={_fmt(t['tpr'])} TNR={_fmt(t['tnr'])} Counts: TP={t['counts']['TP']} TN={t['counts']['TN']} FP={t['counts']['FP']} FN={t['counts']['FN']}"
    )
    print(f"Wrote: {out_json}")

    class _LiteralStr(str):
        pass

    class _LiteralDumper(yaml.SafeDumper):
        pass

    def _lit_representer(dumper, data):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")

    _LiteralDumper.add_representer(_LiteralStr, _lit_representer)
    instr_val = (
        best.get("instructions") if isinstance(best.get("instructions"), str) else ""
    )
    tmpl_val = best.get("template") if isinstance(best.get("template"), str) else ""
    instr_node = _LiteralStr(instr_val) if ("\n" in instr_val) else instr_val
    tmpl_node = _LiteralStr(tmpl_val) if ("\n" in tmpl_val) else tmpl_val
    override = {
        "judges": {
            jkey: {
                "agent": {"instructions": instr_node},
                "input": {"template": tmpl_node},
            }
        }
    }
    out_yml = run_dir / f"{jkey}_override.yml"
    out_yml.write_text(
        yaml.dump(override, Dumper=_LiteralDumper, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"Judge override written: {out_yml}")
    applied = False
    apply_error = None
    if bool(apply):
        try:
            EvalWorkspace(proj_path).update_judge(
                jkey,
                instructions=(
                    best.get("instructions")
                    if isinstance(best.get("instructions"), str)
                    else None
                ),
                template=(
                    best.get("template")
                    if isinstance(best.get("template"), str)
                    else None
                ),
            )
            applied = True
        except Exception as exc:  # noqa: BLE001 - reported, the run's results stand
            apply_error = str(exc)
            print(f"Could not apply the optimized judge: {apply_error}")
    return {
        "run_dir": str(run_dir),
        "out_json": str(out_json),
        "dev_accuracy": dev_acc,
        "test_accuracy": test_acc,
        "judge": jkey,
        "instructions": (
            best.get("instructions")
            if isinstance(best.get("instructions"), str)
            else None
        ),
        "template": (
            best.get("template") if isinstance(best.get("template"), str) else None
        ),
        "applied": applied,
        "apply_error": apply_error,
    }
