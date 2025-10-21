from __future__ import annotations

import json
from pathlib import Path
import re
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
import yaml
from omniagents.core.eval.stratified_sampler import StratifiedBatchSampler


def optimize_agent_from_project(
    proj_path: str,
    agent_key: str | None,
    category: str | None,
    train_file: str | None,
    dev_file: str | None,
    test_file: str | None,
    name: str | None,
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
) -> Dict[str, Any]:
    try:
        import gepa  # type: ignore
    except Exception:
        raise SystemExit(
            "This command requires the 'gepa' package. Install with: pip install gepa[full]"
        )
    from omniagents.core.eval.gepa_agent_adapter import OmniAgentsAgentAdapter
    from omniagents.core.project.compiler import compile_project
    from omniagents.core.project.apply import apply_agent_instructions

    compiled_cfg_path, _graph = compile_project(
        str(proj_path),
        select=None,
        exclude=None,
        agent_key=(str(agent_key or "") or None),
    )
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
            return None
        name_arg = (name or "").strip()
        if not name_arg:
            return None
        candidate = (
            base / "artifacts" / "eval" / name_arg / "splits" / cat / f"{which}.yml"
        ).resolve()
        return candidate if candidate.exists() else None

    tr_path = (
        _resolve_split("train", train_file)
        if (cat or not train_file)
        else (
            _P(train_file)
            if _P(train_file).is_absolute()
            else (base / train_file).resolve()
        )
    )
    dv_path = (
        _resolve_split("dev", dev_file)
        if (cat or not dev_file)
        else (
            _P(dev_file) if _P(dev_file).is_absolute() else (base / dev_file).resolve()
        )
    )
    ts_path = (
        _resolve_split("test", test_file)
        if (cat or not test_file)
        else (
            _P(test_file)
            if _P(test_file).is_absolute()
            else (base / test_file).resolve()
        )
    )

    if not any([tr_path, dv_path, ts_path]):
        if not any([train_file, dev_file, test_file]) and not ((name or "").strip()):
            raise SystemExit(
                "Could not locate scenario splits for category '{cat}'. Provide --name <scenario_set> or explicit split files.".format(
                    cat=cat or "<unknown>"
                )
            )
        expected_dir = None
        if (name or "").strip() and (cat or "").strip():
            expected_dir = (
                base
                / "artifacts"
                / "eval"
                / (name or "").strip()
                / "splits"
                / (cat or "").strip()
            ).resolve()
        if expected_dir is not None:
            raise SystemExit(
                "Could not locate scenario splits for category '{cat}' under {path}. Verify --name or provide explicit split files.".format(
                    cat=cat or "<unknown>", path=expected_dir
                )
            )
        raise SystemExit(
            "Could not locate scenario splits. Provide --name <scenario_set> or explicit split files."
        )

    reference_split_path = dv_path or tr_path or ts_path

    def _read_scenarios(p: _P) -> List[Dict[str, Any]]:
        y = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        items = [x for x in (y.get("scenarios") or []) if isinstance(x, dict)]
        return items

    train_raw = _read_scenarios(tr_path) if tr_path and tr_path.exists() else []
    dev_raw = _read_scenarios(dv_path) if dv_path and dv_path.exists() else []
    test_raw = _read_scenarios(ts_path) if ts_path and ts_path.exists() else []
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

        cfg_all_for_guard = (
            yaml.safe_load(_P(compiled_cfg_path).read_text(encoding="utf-8")) or {}
        )
        ev_guard = cfg_all_for_guard.get("evaluation") or {}
        measures_for_cat = list((_fc_map_guard(ev_guard) or {}).get(cat) or [])
        if not measures_for_cat:
            raise SystemExit(
                f"Failure category '{cat}' has no measures configured in evaluations/evaluation.yml. Add measures to enable scoring."
            )
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
        cfg_tr = yaml.safe_load(_P(compiled_cfg_path).read_text(encoding="utf-8")) or {}
        tracing = cfg_tr.get("tracing") or {}
        if str(tracing.get("provider", "")).lower() in {"studio", "agentio"}:
            base_name = tracing.get("name") or _P(compiled_cfg_path).stem
            if reference_split_path is not None:
                ts = reference_split_path.parents[2].name
            else:
                ts = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
            if not re.fullmatch(r"\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}", ts):
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
    )
    out_dir = output_dir or ""
    if out_dir:
        outp = _P(out_dir)
        base_run = outp if outp.is_absolute() else (base / outp).resolve()
    else:
        if reference_split_path is None:
            raise SystemExit(
                "No scenario splits available; provide --name <scenario_set> or explicit split files."
            )
        base_run = (
            reference_split_path.parents[2] / "agent_optimize" / (cat or "category")
        )
    if resume:
        run_dir = base_run
    else:
        from datetime import datetime as _dt

        run_dir = base_run / _dt.now().strftime("%Y%m%d%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        cfg_tr = yaml.safe_load(_P(compiled_cfg_path).read_text(encoding="utf-8")) or {}
        tracing = cfg_tr.get("tracing") or {}
        if (
            str(tracing.get("provider", "")).lower() in {"studio", "agentio"}
            and workflow_name
        ):
            try:
                from omniagents.studio import tracing as studio_tracing

                tracing_module = studio_tracing
            except ImportError:
                import agentio as legacy_tracing  # type: ignore

                tracing_module = legacy_tracing
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
        from gepa.logging.experiment_tracker import create_experiment_tracker  # type: ignore
        from gepa.strategies.candidate_selector import ParetoCandidateSelector  # type: ignore
        from gepa.strategies.component_selector import RoundRobinReflectionComponentSelector  # type: ignore
        from gepa.utils import FileStopper, CompositeStopper, MaxMetricCallsStopper  # type: ignore
        from gepa.proposer.reflective_mutation.reflective_mutation import ReflectiveMutationProposer  # type: ignore
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
        reflection_lm=str(reflection_model or "gpt-4.1"),
    )

    def _evaluator(inputs, prog):
        ev = adapter.evaluate(inputs, prog, capture_traces=False)
        return ev.outputs, ev.scores

    try:
        adapter.debug_dir = run_dir / "debug"
    except Exception:
        pass

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
    if bool(apply):
        try:
            apply_agent_instructions(
                proj_path,
                agent_key,
                (
                    best.get("instructions")
                    if isinstance(best.get("instructions"), str)
                    else None
                ),
            )
        except Exception:
            pass
    return {
        "run_dir": str(run_dir),
        "out_json": str(out_json),
        "dev_score": dev_acc,
        "test_score": test_acc,
        "category": cat,
        "instructions": (
            best.get("instructions")
            if isinstance(best.get("instructions"), str)
            else None
        ),
        "log_file": str(log_path),
    }


def optimize_judge_from_project(
    proj_path: str,
    agent_key: str | None,
    category: str | None,
    train_file: str | None,
    dev_file: str | None,
    test_file: str | None,
    name: str | None,
    judge_key: str | None,
    runs_file: str | None,
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
    from omniagents.core.project.compiler import compile_project
    from omniagents.core.project.apply import apply_judge_updates

    compiled_cfg_path, _graph = compile_project(
        str(proj_path),
        select=None,
        exclude=None,
        agent_key=(str(agent_key or "") or None),
    )
    _P = Path
    base = _P(proj_path).resolve()
    if base.is_file():
        base = base.parent
    cat = (category or "").strip()

    def _resolve_split(which, explicit):
        if explicit:
            p = _P(explicit)
            return p if p.is_absolute() else (base / p).resolve()
        name_arg = (name or "").strip()
        if not name_arg:
            raise SystemExit(
                "Provide --name <scenario_set> to locate splits or pass explicit split files."
            )
        candidate = (
            base / "artifacts" / "eval" / name_arg / "splits" / cat / f"{which}.yml"
        ).resolve()
        if candidate.exists():
            return candidate
        raise SystemExit(
            f"Could not find split for category '{cat}' ({which}) at {candidate}."
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

    def _default_runs_path(sp: _P) -> _P:
        return sp.parents[2] / "generated_runs.json"

    if runs_file:
        rf = _P(runs_file)
        runs_path = rf if rf.is_absolute() else (base / rf).resolve()
    else:
        runs_path = _default_runs_path(dv_path)
    if not runs_path.exists():
        raise SystemExit(
            f"Missing generated runs fixtures: {runs_path}. Generate scenarios, then run with 'omniagents eval scenarios run'."
        )

    jkey = judge_key
    if not jkey:
        try:
            from omniagents.core.eval.pipeline import (
                _failure_category_map as _fcmap,
                _derive_judge_keys_from_measures as _derive_j,
            )

            cfg_detect = (
                yaml.safe_load(_P(compiled_cfg_path).read_text(encoding="utf-8")) or {}
            )
            ev = cfg_detect.get("evaluation") or {}
            measures = list((_fcmap(ev) or {}).get(cat) or [])
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

    cfg = yaml.safe_load(_P(compiled_cfg_path).read_text(encoding="utf-8")) or {}
    jcfg = ((cfg.get("evaluation") or {}).get("judges") or {}).get(jkey) or {}

    def _read_text_file(base_dir: _P, rel: str) -> str:
        p = _P(rel)
        if not p.is_absolute():
            p = (base_dir / p).resolve()
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            return ""

    agent_cfg = jcfg.get("agent") or {}
    input_cfg = jcfg.get("input") or {}
    seed_instructions = agent_cfg.get("instructions") or ""
    if (not seed_instructions) and agent_cfg.get("instructions_file"):
        seed_instructions = _read_text_file(
            _P(compiled_cfg_path).resolve().parent,
            str(agent_cfg.get("instructions_file")),
        )
    seed_template = input_cfg.get("template") or ""
    if (not seed_template) and input_cfg.get("template_file"):
        seed_template = _read_text_file(
            _P(compiled_cfg_path).resolve().parent, str(input_cfg.get("template_file"))
        )
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
        cfg_tr = yaml.safe_load(_P(compiled_cfg_path).read_text(encoding="utf-8")) or {}
        tracing = cfg_tr.get("tracing") or {}
        if str(tracing.get("provider", "")).lower() == "agentio":
            import json as _json

            base_name = tracing.get("name") or _P(compiled_cfg_path).stem
            set_dir = dv_path.parents[2] if dv_path is not None else None
            ts = None
            sc_name = None
            try:
                if set_dir is not None:
                    mf = set_dir / "manifest.json"
                    if mf.exists():
                        md = _json.loads(mf.read_text(encoding="utf-8"))
                        ts = md.get("timestamp")
                        sc_name = md.get("name")
            except Exception:
                ts = None
                sc_name = None
            if not ts:
                ts = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
            if not sc_name:
                try:
                    sc_name = set_dir.name if set_dir is not None else "scenarios"
                except Exception:
                    sc_name = "scenarios"
            workflow_name = f"{base_name}_judge_optimize_{jkey}_{sc_name}_{ts}"
    except Exception:
        workflow_name = None
    adapter = OmniAgentsJudgeAdapter(
        _P(compiled_cfg_path),
        jkey,
        runs_path,
        category=(str(cat) if cat else None),
        source=str(source or "final_assistant_text"),
        judge_max_turns=int(max_turns or 1),
        optimizer_key=(str(optimizer_key) if optimizer_key else None),
        workflow_name=workflow_name,
    )
    out_dir = output_dir or ""
    if out_dir:
        outp = _P(out_dir)
        base_run = outp if outp.is_absolute() else (base / outp).resolve()
    else:
        base_run = dv_path.parents[2] / "judge_optimize" / jkey
    if resume:
        run_dir = base_run
    else:
        from datetime import datetime as _dt

        run_dir = base_run / _dt.now().strftime("%Y%m%d%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        adapter.debug_dir = run_dir / "debug"
    except Exception:
        pass
    try:
        cfg_tr = yaml.safe_load(_P(compiled_cfg_path).read_text(encoding="utf-8")) or {}
        tracing = cfg_tr.get("tracing") or {}
        if str(tracing.get("provider", "")).lower() == "agentio" and workflow_name:
            import agentio  # type: ignore

            project = tracing.get("project") or "default"
            resume_tr = (tracing.get("settings") or {}).get("resume", "never")
            agentio.init(
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
    if bool(apply):
        try:
            apply_judge_updates(
                proj_path,
                jkey,
                (
                    best.get("instructions")
                    if isinstance(best.get("instructions"), str)
                    else None
                ),
                best.get("template") if isinstance(best.get("template"), str) else None,
            )
        except Exception:
            pass
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
    }
