import builtins
import json
from pathlib import Path
import sys
import types

import pytest
import yaml

from omniagents.core.eval import optimize
from omniagents.core.eval.loader import EvalConfig, filter_scenarios


def _make_fake_eval_config(compiled_path, **overrides):
    """Create a minimal EvalConfig for test mocking."""
    p = Path(compiled_path).resolve()
    # Read tracing/eval config from the file if it exists, for test realism
    tracing_config = overrides.get("tracing_config", {})
    eval_cfg = overrides.get("config", {})
    agent_config = overrides.get("agent_config", {})
    if p.is_file() and not (tracing_config or eval_cfg or agent_config):
        try:
            import yaml as _yaml

            raw = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            eval_cfg = raw.get("evaluation") or {}
            tracing_config = raw.get("tracing") or {}
            agent_config = raw
        except Exception:
            pass
    return EvalConfig(
        config=eval_cfg,
        agent_config=agent_config,
        agent_path=p,
        eval_dir=p.parent,
        project_root=p.parent,
        tracing_config=tracing_config,
        project_config={},
    )


@pytest.fixture
def configure_optimize_env(monkeypatch):
    def _setup(
        compiled_path,
        *,
        instructions="optimized",
        total_num_evals=3,
        progress_state=None,
        evaluation_scores=None,
        apply_raises=False,
        best_candidate=None,
        max_stop_should_trigger=False,
    ):
        env = types.SimpleNamespace()
        env.instructions = instructions
        env.total_num_evals = total_num_evals
        env.progress_state = progress_state or types.SimpleNamespace(total_num_evals=1)
        env.evaluation_scores = evaluation_scores
        env.adapter_instances = []
        env.adapter_evaluations = []
        env.proposer_kwargs = []
        env.file_stoppers = []
        env.max_stoppers = []
        env.stopper_calls = []
        env.composite_calls = []
        env.apply_calls = []
        env.apply_attempts = []
        env.engine_kwargs = {}
        env.apply_raises = apply_raises
        env.best_candidate = best_candidate
        env.max_stop_should_trigger = max_stop_should_trigger
        env.stop_callback_result = None

        def fake_load_eval_config(*args, **kwargs):
            return _make_fake_eval_config(compiled_path)

        monkeypatch.setattr(
            "omniagents.core.eval.loader.load_eval_config",
            fake_load_eval_config,
        )

        def fake_apply(*args, **kwargs):
            env.apply_attempts.append((args, kwargs))
            if env.apply_raises:
                raise RuntimeError("apply failed")
            env.apply_calls.append((args, kwargs))

        monkeypatch.setattr(
            "omniagents.core.project.agent_files.update_agent",
            fake_apply,
        )

        class DummyAdapter:
            def __init__(self, cfg_path, category, **kwargs):
                self.cfg_path = cfg_path
                self.category = category
                self.kwargs = kwargs
                self.evaluate_calls = []
                env.adapter_instances.append(self)

            def evaluate(self, dataset, candidate, capture_traces=False):
                self.evaluate_calls.append((dataset, candidate))
                env.adapter_evaluations.append((dataset, candidate))
                if env.evaluation_scores:
                    outputs, scores = env.evaluation_scores(dataset, candidate)
                else:
                    scores = [0.8 for _ in dataset]
                    outputs = dataset
                return types.SimpleNamespace(outputs=outputs, scores=scores)

        monkeypatch.setattr(
            "omniagents.core.eval.gepa_agent_adapter.OmniAgentsAgentAdapter",
            DummyAdapter,
        )

        dummy_gepa = types.ModuleType("gepa")
        dummy_gepa.__path__ = []
        monkeypatch.setitem(sys.modules, "gepa", dummy_gepa)

        core_pkg = types.ModuleType("gepa.core")
        core_pkg.__path__ = []
        monkeypatch.setitem(sys.modules, "gepa.core", core_pkg)

        data_loader_mod = types.ModuleType("gepa.core.data_loader")
        # Identity: tests hand plain lists around and assert on them.
        data_loader_mod.ensure_loader = lambda dataset: dataset
        monkeypatch.setitem(sys.modules, "gepa.core.data_loader", data_loader_mod)
        core_pkg.data_loader = data_loader_mod

        class DummyEngine:
            def __init__(self, **kwargs):
                env.engine_kwargs = kwargs

            def run(self):
                stop_cb = env.engine_kwargs.get("stop_callback")
                if stop_cb:
                    env.stop_callback_result = stop_cb(env.progress_state)
                candidate = env.best_candidate
                if candidate is None:
                    candidate = {"instructions": env.instructions}
                return types.SimpleNamespace(
                    total_num_evals=env.total_num_evals,
                    best_candidate=candidate,
                )

        engine_mod = types.ModuleType("gepa.core.engine")
        engine_mod.GEPAEngine = DummyEngine
        monkeypatch.setitem(sys.modules, "gepa.core.engine", engine_mod)
        core_pkg.engine = engine_mod

        class DummyResult:
            @staticmethod
            def from_state(state):
                return types.SimpleNamespace(best_candidate=state.best_candidate)

        result_mod = types.ModuleType("gepa.core.result")
        result_mod.GEPAResult = DummyResult
        monkeypatch.setitem(sys.modules, "gepa.core.result", result_mod)
        core_pkg.result = result_mod

        class DummyTracker:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        tracker_mod = types.ModuleType("gepa.logging.experiment_tracker")

        def create_experiment_tracker(**kwargs):
            return DummyTracker()

        tracker_mod.create_experiment_tracker = create_experiment_tracker
        logging_pkg = types.ModuleType("gepa.logging")
        logging_pkg.__path__ = []
        logging_pkg.experiment_tracker = tracker_mod
        logger_mod = types.ModuleType("gepa.logging.logger")

        class StdOutLogger:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def log(self, *args, **kwargs):
                return None

        logger_mod.StdOutLogger = StdOutLogger
        logging_pkg.logger = logger_mod
        monkeypatch.setitem(sys.modules, "gepa.logging", logging_pkg)
        monkeypatch.setitem(sys.modules, "gepa.logging.experiment_tracker", tracker_mod)
        monkeypatch.setitem(sys.modules, "gepa.logging.logger", logger_mod)
        dummy_gepa.logging = logging_pkg

        strategies_pkg = types.ModuleType("gepa.strategies")
        strategies_pkg.__path__ = []

        class DummyCandidateSelector:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        selector_mod = types.ModuleType("gepa.strategies.candidate_selector")
        selector_mod.ParetoCandidateSelector = DummyCandidateSelector
        monkeypatch.setitem(
            sys.modules,
            "gepa.strategies.candidate_selector",
            selector_mod,
        )

        class DummyComponentSelector:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

        component_mod = types.ModuleType("gepa.strategies.component_selector")
        component_mod.RoundRobinReflectionComponentSelector = DummyComponentSelector
        monkeypatch.setitem(
            sys.modules,
            "gepa.strategies.component_selector",
            component_mod,
        )

        strategies_pkg.candidate_selector = selector_mod
        strategies_pkg.component_selector = component_mod
        monkeypatch.setitem(sys.modules, "gepa.strategies", strategies_pkg)
        dummy_gepa.strategies = strategies_pkg

        class _Stopper:
            def __init__(self, label, *args, **kwargs):
                self.label = label
                self.args = args
                self.kwargs = kwargs

            def __call__(self, state):
                env.stopper_calls.append((self.label, state))
                should_stop = False
                if self.label == "max" and env.max_stop_should_trigger:
                    should_stop = True
                return should_stop

        class _Composite:
            def __init__(self, *stoppers):
                self.stoppers = stoppers

            def __call__(self, state):
                env.composite_calls.append(state)
                result = False
                for stopper in self.stoppers:
                    result = stopper(state) or result
                return result

        def file_stopper_factory(*args, **kwargs):
            stopper = _Stopper("file", *args, **kwargs)
            env.file_stoppers.append(stopper)
            return stopper

        def max_stopper_factory(*args, **kwargs):
            stopper = _Stopper("max", *args, **kwargs)
            env.max_stoppers.append(stopper)
            return stopper

        def composite_factory(*stoppers):
            return _Composite(*stoppers)

        utils_mod = types.ModuleType("gepa.utils")
        utils_mod.FileStopper = file_stopper_factory
        utils_mod.MaxMetricCallsStopper = max_stopper_factory
        utils_mod.CompositeStopper = composite_factory
        monkeypatch.setitem(sys.modules, "gepa.utils", utils_mod)
        dummy_gepa.utils = utils_mod

        proposer_pkg = types.ModuleType("gepa.proposer")
        proposer_pkg.__path__ = []
        reflective_pkg = types.ModuleType("gepa.proposer.reflective_mutation")
        reflective_pkg.__path__ = []

        class DummyProposer:
            def __init__(self, **kwargs):
                env.proposer_kwargs.append(kwargs)

        reflective_mod = types.ModuleType(
            "gepa.proposer.reflective_mutation.reflective_mutation"
        )
        reflective_mod.ReflectiveMutationProposer = DummyProposer
        reflective_pkg.reflective_mutation = reflective_mod
        proposer_pkg.reflective_mutation = reflective_pkg
        monkeypatch.setitem(sys.modules, "gepa.proposer", proposer_pkg)
        monkeypatch.setitem(
            sys.modules,
            "gepa.proposer.reflective_mutation",
            reflective_pkg,
        )
        monkeypatch.setitem(
            sys.modules,
            "gepa.proposer.reflective_mutation.reflective_mutation",
            reflective_mod,
        )
        dummy_gepa.proposer = proposer_pkg
        dummy_gepa.core = core_pkg

        return env

    return _setup


def create_split(path, labels, category="safety"):
    data = {
        "scenarios": [
            {
                "id": f"{path.stem}-{idx}",
                "label": lbl,
                "name": f"scenario-{idx}",
                "prompt": "prompt",
                "target_category": category,
            }
            for idx, lbl in enumerate(labels)
        ]
    }
    path.write_text(yaml.dump(data, sort_keys=False), encoding="utf-8")


def setup_optimize_stubs(
    monkeypatch,
    compiled_path,
    *,
    best_instructions="optimized",
    total_num_evals=4,
    stop_should_stop=False,
    score_value=0.8,
):
    adapter_instances = []
    engine_instances = []
    engine_stop_returns = []
    file_stop_states = []
    max_stop_states = []
    composite_states = []
    apply_calls = []

    class DummyAdapter:
        def __init__(self, cfg_path, category, **kwargs):
            self.cfg_path = cfg_path
            self.category = category
            self.kwargs = kwargs
            self.evaluate_calls = []
            adapter_instances.append(self)

        def evaluate(self, dataset, candidate, capture_traces=False):
            self.evaluate_calls.append((dataset, candidate))
            scores = [score_value for _ in dataset]
            return types.SimpleNamespace(outputs=dataset, scores=scores)

    adapter_module = types.ModuleType("omniagents.core.eval.gepa_agent_adapter")
    adapter_module.OmniAgentsAgentAdapter = DummyAdapter
    adapter_module.__package__ = "omniagents.core.eval"
    monkeypatch.setitem(
        sys.modules,
        "omniagents.core.eval.gepa_agent_adapter",
        adapter_module,
    )

    def fake_load_eval_config(*args, **kwargs):
        return _make_fake_eval_config(compiled_path)

    loader_module = types.ModuleType("omniagents.core.eval.loader")
    loader_module.load_eval_config = fake_load_eval_config
    loader_module.EvalConfig = EvalConfig
    loader_module.filter_scenarios = filter_scenarios
    loader_module.__package__ = "omniagents.core.eval"
    monkeypatch.setitem(
        sys.modules,
        "omniagents.core.eval.loader",
        loader_module,
    )

    def fake_apply(*args, **kwargs):
        apply_calls.append((args, kwargs))
        return None

    apply_module = types.ModuleType("omniagents.core.project.agent_files")
    apply_module.update_agent = fake_apply
    apply_module.__package__ = "omniagents.core.project"
    monkeypatch.setitem(
        sys.modules,
        "omniagents.core.project.agent_files",
        apply_module,
    )

    dummy_gepa = types.ModuleType("gepa")
    dummy_gepa.__path__ = []
    monkeypatch.setitem(sys.modules, "gepa", dummy_gepa)

    core_pkg = types.ModuleType("gepa.core")
    core_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "gepa.core", core_pkg)

    data_loader_mod = types.ModuleType("gepa.core.data_loader")
    data_loader_mod.ensure_loader = lambda dataset: dataset
    monkeypatch.setitem(sys.modules, "gepa.core.data_loader", data_loader_mod)
    core_pkg.data_loader = data_loader_mod

    class DummyEngine:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            engine_instances.append(self)

        def run(self):
            stop_callback = self.kwargs.get("stop_callback")
            if stop_callback:
                state_for_stop = types.SimpleNamespace(
                    total_num_evals=2,
                    best_candidate={"instructions": best_instructions},
                )
                engine_stop_returns.append(stop_callback(state_for_stop))
            return types.SimpleNamespace(
                total_num_evals=total_num_evals,
                best_candidate={"instructions": best_instructions},
            )

    engine_mod = types.ModuleType("gepa.core.engine")
    engine_mod.GEPAEngine = DummyEngine
    monkeypatch.setitem(sys.modules, "gepa.core.engine", engine_mod)
    core_pkg.engine = engine_mod

    class DummyResult:
        @staticmethod
        def from_state(state):
            return types.SimpleNamespace(best_candidate=state.best_candidate)

    result_mod = types.ModuleType("gepa.core.result")
    result_mod.GEPAResult = DummyResult
    monkeypatch.setitem(sys.modules, "gepa.core.result", result_mod)
    core_pkg.result = result_mod

    class DummyTracker:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    tracker_mod = types.ModuleType("gepa.logging.experiment_tracker")

    def create_experiment_tracker(**kwargs):
        return DummyTracker()

    tracker_mod.create_experiment_tracker = create_experiment_tracker
    logging_pkg = types.ModuleType("gepa.logging")
    logging_pkg.__path__ = []
    logging_pkg.experiment_tracker = tracker_mod
    monkeypatch.setitem(sys.modules, "gepa.logging", logging_pkg)
    monkeypatch.setitem(sys.modules, "gepa.logging.experiment_tracker", tracker_mod)
    dummy_gepa.logging = logging_pkg

    strategies_pkg = types.ModuleType("gepa.strategies")
    strategies_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "gepa.strategies", strategies_pkg)

    selector_mod = types.ModuleType("gepa.strategies.candidate_selector")

    class DummyCandidateSelector:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    selector_mod.ParetoCandidateSelector = DummyCandidateSelector
    monkeypatch.setitem(sys.modules, "gepa.strategies.candidate_selector", selector_mod)
    strategies_pkg.candidate_selector = selector_mod

    component_mod = types.ModuleType("gepa.strategies.component_selector")

    class DummyComponentSelector:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    component_mod.RoundRobinReflectionComponentSelector = DummyComponentSelector
    monkeypatch.setitem(
        sys.modules,
        "gepa.strategies.component_selector",
        component_mod,
    )
    strategies_pkg.component_selector = component_mod

    utils_mod = types.ModuleType("gepa.utils")

    class DummyFileStopper:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, state):
            file_stop_states.append(state)
            return False

    class DummyMaxMetricCallsStopper:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, state):
            max_stop_states.append(state)
            return stop_should_stop

    class DummyCompositeStopper:
        def __init__(self, *stoppers):
            self.stoppers = stoppers

        def __call__(self, state):
            composite_states.append(state)
            return any(stopper(state) for stopper in self.stoppers)

    utils_mod.FileStopper = DummyFileStopper
    utils_mod.MaxMetricCallsStopper = DummyMaxMetricCallsStopper
    utils_mod.CompositeStopper = DummyCompositeStopper
    monkeypatch.setitem(sys.modules, "gepa.utils", utils_mod)
    dummy_gepa.utils = utils_mod

    proposer_pkg = types.ModuleType("gepa.proposer")
    proposer_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "gepa.proposer", proposer_pkg)

    reflective_pkg = types.ModuleType("gepa.proposer.reflective_mutation")
    reflective_pkg.__path__ = []
    monkeypatch.setitem(
        sys.modules,
        "gepa.proposer.reflective_mutation",
        reflective_pkg,
    )

    reflective_mod = types.ModuleType(
        "gepa.proposer.reflective_mutation.reflective_mutation"
    )

    class DummyProposer:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    reflective_mod.ReflectiveMutationProposer = DummyProposer
    monkeypatch.setitem(
        sys.modules,
        "gepa.proposer.reflective_mutation.reflective_mutation",
        reflective_mod,
    )
    reflective_pkg.reflective_mutation = reflective_mod
    proposer_pkg.reflective_mutation = reflective_pkg

    dummy_gepa.core = core_pkg
    dummy_gepa.strategies = strategies_pkg
    dummy_gepa.proposer = proposer_pkg

    return types.SimpleNamespace(
        adapter_instances=adapter_instances,
        engine_instances=engine_instances,
        engine_stop_returns=engine_stop_returns,
        file_stop_states=file_stop_states,
        max_stop_states=max_stop_states,
        composite_states=composite_states,
        apply_calls=apply_calls,
    )


def test_optimize_agent_requires_gepa(monkeypatch, tmp_path):
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "gepa":
            raise ModuleNotFoundError("gepa")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(SystemExit) as exc:
        optimize.optimize_agent_from_project(
            str(tmp_path),
            None,
            None,
            None,
            None,
            None,
            False,
            None,
            10,
            "gpt-4o-mini",
            "fraction",
            None,
            0,
            None,
            False,
            False,
        )

    assert "This command requires the 'gepa' package" in str(exc.value)


def test_optimize_agent_missing_splits(monkeypatch, tmp_path):
    compiled_path = tmp_path / "compiled.yaml"
    compiled_path.write_text(
        ("""
evaluation:
  failure_categories:
    safety:
      measures:
        - safety_agent
  metrics: []
        """.strip()),
        encoding="utf-8",
    )

    def fake_load_eval_config(*args, **kwargs):
        return _make_fake_eval_config(compiled_path)

    dummy_gepa = types.ModuleType("gepa")
    monkeypatch.setitem(sys.modules, "gepa", dummy_gepa)

    adapter_module = types.ModuleType("omniagents.core.eval.gepa_agent_adapter")

    class DummyAdapter:
        def __init__(self, *args, **kwargs):
            pass

    adapter_module.OmniAgentsAgentAdapter = DummyAdapter
    adapter_module.__package__ = "omniagents.core.eval"
    monkeypatch.setitem(
        sys.modules,
        "omniagents.core.eval.gepa_agent_adapter",
        adapter_module,
    )

    loader_module = types.ModuleType("omniagents.core.eval.loader")
    loader_module.load_eval_config = fake_load_eval_config
    loader_module.EvalConfig = EvalConfig
    loader_module.filter_scenarios = filter_scenarios
    loader_module.__package__ = "omniagents.core.eval"
    monkeypatch.setitem(
        sys.modules,
        "omniagents.core.eval.loader",
        loader_module,
    )

    apply_module = types.ModuleType("omniagents.core.project.agent_files")
    apply_module.update_agent = lambda *args, **kwargs: None
    apply_module.__package__ = "omniagents.core.project"
    monkeypatch.setitem(
        sys.modules,
        "omniagents.core.project.agent_files",
        apply_module,
    )

    with pytest.raises(SystemExit) as exc:
        optimize.optimize_agent_from_project(
            str(tmp_path),
            None,
            "safety",
            None,
            None,
            None,
            False,
            None,
            10,
            "gpt-4o-mini",
            "fraction",
            None,
            0,
            None,
            False,
            False,
        )

    assert "No judge dataset for category 'safety'" in str(exc.value)


def test_optimize_judge_requires_gepa(monkeypatch, tmp_path):
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "gepa":
            raise ModuleNotFoundError("gepa")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(SystemExit) as exc:
        optimize.optimize_judge_from_project(
            str(tmp_path),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "final_assistant_text",
            1,
            5,
            None,
            "instructions",
            None,
            0,
            None,
            False,
            False,
        )

    assert "This command requires the 'gepa' package" in str(exc.value)


def test_optimize_agent_happy_path(monkeypatch, tmp_path, configure_optimize_env):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    compiled_path = project_dir / "compiled.yaml"
    compiled_path.write_text(
        yaml.dump(
            {
                "evaluation": {
                    "failure_categories": {"safety": {"measures": ["safety_agent"]}},
                    "judges": {},
                },
                "instructions": "Base",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    env = configure_optimize_env(compiled_path)

    def write_split(path, labels):
        path.write_text(
            yaml.dump(
                {
                    "scenarios": [
                        {
                            "id": f"{path.stem}-{idx}",
                            "label": lbl,
                            "name": f"scenario-{idx}",
                            "prompt": "prompt",
                            "target_category": "safety",
                        }
                        for idx, lbl in enumerate(labels)
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    train_path = tmp_path / "train.yml"
    dev_path = tmp_path / "dev.yml"
    test_path = tmp_path / "test.yml"
    write_split(train_path, [True, False])
    write_split(dev_path, [True])
    write_split(test_path, [False])

    logs = []

    result = optimize.optimize_agent_from_project(
        str(project_dir),
        None,
        None,
        str(train_path),
        str(dev_path),
        str(test_path),
        False,
        5,
        3,
        "gpt-4o-mini",
        "fraction",
        None,
        0,
        str(tmp_path / "output"),
        False,
        False,
        log_hook=logs.append,
    )

    assert result["category"] == "safety"
    assert result["instructions"] == env.instructions
    assert env.adapter_instances[0].evaluate_calls
    assert any(msg.startswith("LOG_PATH:") for msg in logs)


def test_optimize_agent_missing_named_split_path(
    monkeypatch, tmp_path, configure_optimize_env
):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    compiled_path = project_dir / "compiled.yaml"
    compiled_path.write_text(
        yaml.dump(
            {
                "evaluation": {
                    "failure_categories": {"safety": {"measures": ["safety_agent"]}},
                    "judges": {},
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    configure_optimize_env(compiled_path)

    with pytest.raises(SystemExit) as exc:
        optimize.optimize_agent_from_project(
            str(project_dir),
            None,
            "safety",
            None,
            None,
            None,
            False,
            1,
            1,
            "gpt-4o-mini",
            "fraction",
            None,
            0,
            None,
            False,
            False,
        )

    assert "judge-datasets/safety" in str(exc.value)


def test_optimize_agent_missing_category_from_splits(
    monkeypatch, tmp_path, configure_optimize_env
):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    compiled_path = project_dir / "compiled.yaml"
    compiled_path.write_text(
        yaml.dump(
            {
                "evaluation": {
                    "failure_categories": {"safety": {"measures": ["safety_agent"]}},
                    "judges": {},
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    configure_optimize_env(compiled_path)

    def write_split(path):
        path.write_text(
            yaml.dump(
                {
                    "scenarios": [
                        {
                            "id": f"{path.stem}-0",
                            "label": True,
                            "name": "scenario",
                            "prompt": "prompt",
                        }
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    train_path = tmp_path / "train.yml"
    dev_path = tmp_path / "dev.yml"
    test_path = tmp_path / "test.yml"
    write_split(train_path)
    write_split(dev_path)
    write_split(test_path)

    with pytest.raises(SystemExit) as exc:
        optimize.optimize_agent_from_project(
            str(project_dir),
            None,
            None,
            str(train_path),
            str(dev_path),
            str(test_path),
            False,
            1,
            1,
            "gpt-4o-mini",
            "fraction",
            None,
            0,
            None,
            False,
            False,
        )

    assert "please provide --category" in str(exc.value)


def test_optimize_agent_auto_resolution_resume_apply(
    monkeypatch, tmp_path, configure_optimize_env
):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    compiled_path = project_dir / "compiled.yaml"
    compiled_path.write_text(
        yaml.dump(
            {
                "evaluation": {
                    "failure_categories": {"safety": {"measures": ["safety_agent"]}},
                    "judges": {},
                },
                "instructions": "Seed",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    env = configure_optimize_env(
        compiled_path,
        instructions="updated",
        total_num_evals=4,
        progress_state=types.SimpleNamespace(total_num_evals=1),
    )

    scenario_dir = project_dir / "judge-datasets" / "safety"
    scenario_dir.mkdir(parents=True)

    def write_split(path, labels):
        path.write_text(
            yaml.dump(
                {
                    "scenarios": [
                        {
                            "id": f"{path.stem}-{idx}",
                            "label": lbl,
                            "name": f"scenario-{idx}",
                            "prompt": "prompt",
                            "target_category": "safety",
                        }
                        for idx, lbl in enumerate(labels)
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    write_split(scenario_dir / "train.yml", [True, False, True])
    write_split(scenario_dir / "dev.yml", [True, False])
    write_split(scenario_dir / "test.yml", [False, True])

    logs = []

    result = optimize.optimize_agent_from_project(
        str(project_dir),
        None,
        "safety",
        None,
        None,
        None,
        True,
        5,
        4,
        "gpt-4o-mini",
        "fraction",
        None,
        7,
        None,
        False,
        True,
        log_hook=logs.append,
    )

    run_dir = Path(result["run_dir"])
    assert run_dir.parent == project_dir / "optimizations"
    assert run_dir.name.endswith("_agent_safety")
    assert env.apply_calls
    apply_args, apply_kwargs = env.apply_calls[0]
    assert apply_args[0] == str(project_dir)
    assert apply_kwargs == {"instructions": "updated"}
    assert result["applied"] is True
    assert env.proposer_kwargs
    trainset = env.proposer_kwargs[0]["trainset"]
    assert all(item.get("label") is True for item in trainset)
    assert len(trainset) == 2
    assert env.composite_calls
    assert env.stopper_calls
    labels = {label for label, _ in env.stopper_calls}
    assert labels == {"file", "max"}
    progress_messages = [msg for msg in logs if msg.startswith("PROGRESS:")]
    assert any(
        msg.split(":")[2] not in {"", "0"}
        for msg in progress_messages
        if len(msg.split(":")) == 3
    )
    env.engine_kwargs["logger"].log("extra-log")
    # The agent engine takes the adapter directly now (no evaluator kwarg);
    # dev/test scoring goes through adapter.evaluate, asserted above via
    # env.adapter_evaluations.
    assert env.engine_kwargs["adapter"] is env.adapter_instances[0]


def test_optimize_agent_auto_resolve_resume_and_progress_logging(monkeypatch, tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    scenario_dir = project_dir / "judge-datasets" / "safety"
    scenario_dir.mkdir(parents=True)
    (project_dir / "optimizations" / "earlier").mkdir(parents=True)
    compiled_path = project_dir / "compiled.yaml"
    compiled_path.write_text(
        yaml.dump(
            {
                "evaluation": {
                    "failure_categories": {"safety": {"measures": ["safety_agent"]}},
                    "judges": {},
                },
                "instructions": "Seed",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    ctx = setup_optimize_stubs(
        monkeypatch,
        compiled_path,
        stop_should_stop=True,
        total_num_evals=4,
    )
    train_path = scenario_dir / "train.yml"
    dev_path = scenario_dir / "dev.yml"
    test_path = scenario_dir / "test.yml"
    create_split(train_path, [True, False, True])
    create_split(dev_path, [True, False])
    create_split(test_path, [False, False])
    logs = []
    result = optimize.optimize_agent_from_project(
        str(project_dir),
        "agent-key",
        "safety",
        None,
        None,
        None,
        True,
        5,
        3,
        "gpt-4o-mini",
        "fraction",
        None,
        0,
        str(project_dir / "optimizations" / "earlier"),
        True,
        False,
        log_hook=logs.append,
    )
    # Resuming continues in the optimization named.
    assert Path(result["run_dir"]) == project_dir / "optimizations" / "earlier"
    assert any(msg.startswith("LOG_PATH:") for msg in logs)
    assert any(msg.startswith("PROGRESS:2:") for msg in logs)
    assert logs[-1] == "PROGRESS:4:0"
    dev_dataset = ctx.adapter_instances[0].evaluate_calls[0][0]
    assert dev_dataset and all(item["label"] for item in dev_dataset)
    test_dataset = ctx.adapter_instances[0].evaluate_calls[1][0]
    assert test_dataset == []
    assert ctx.max_stop_states
    assert ctx.engine_stop_returns == [True]


def test_optimize_agent_positives_only_filters_all_splits(
    monkeypatch, tmp_path, configure_optimize_env
):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    compiled_path = project_dir / "compiled.yaml"
    compiled_path.write_text(
        yaml.dump(
            {
                "evaluation": {
                    "failure_categories": {"safety": {"measures": ["safety_agent"]}},
                    "judges": {},
                },
                "instructions": "Seed",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    env = configure_optimize_env(compiled_path)

    def write_split(path, labels):
        path.write_text(
            yaml.dump(
                {
                    "scenarios": [
                        {
                            "id": f"{path.stem}-{idx}",
                            "label": lbl,
                            "name": f"scenario-{idx}",
                            "prompt": "prompt",
                            "target_category": "safety",
                        }
                        for idx, lbl in enumerate(labels)
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    train_path = tmp_path / "train.yml"
    dev_path = tmp_path / "dev.yml"
    test_path = tmp_path / "test.yml"
    write_split(train_path, [True, False, True])
    write_split(dev_path, [False, True])
    write_split(test_path, [True, False])

    logs = []

    result = optimize.optimize_agent_from_project(
        str(project_dir),
        None,
        None,
        str(train_path),
        str(dev_path),
        str(test_path),
        True,
        4,
        3,
        "gpt-4o-mini",
        "fraction",
        None,
        13,
        str(tmp_path / "runs"),
        False,
        False,
        log_hook=logs.append,
    )

    assert result["instructions"] == env.instructions
    assert len(env.adapter_evaluations) >= 2
    dev_dataset = env.adapter_evaluations[-2][0]
    test_dataset = env.adapter_evaluations[-1][0]
    assert all(item.get("label") is True for item in dev_dataset)
    assert all(item.get("label") is True for item in test_dataset)
    assert logs
    assert logs[-1] == f"PROGRESS:{env.total_num_evals}:0"


def test_optimize_agent_stop_callback_respects_max_stopper(
    monkeypatch, tmp_path, configure_optimize_env
):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    compiled_path = project_dir / "compiled.yaml"
    compiled_path.write_text(
        yaml.dump(
            {
                "evaluation": {
                    "failure_categories": {"safety": {"measures": ["safety_agent"]}},
                    "judges": {},
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    env = configure_optimize_env(
        compiled_path,
        total_num_evals=5,
        progress_state=types.SimpleNamespace(total_num_evals=2),
        max_stop_should_trigger=True,
    )

    def write_split(path, labels):
        path.write_text(
            yaml.dump(
                {
                    "scenarios": [
                        {
                            "id": f"{path.stem}-{idx}",
                            "label": lbl,
                            "name": f"scenario-{idx}",
                            "prompt": "prompt",
                            "target_category": "safety",
                        }
                        for idx, lbl in enumerate(labels)
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    train_path = tmp_path / "train.yml"
    dev_path = tmp_path / "dev.yml"
    test_path = tmp_path / "test.yml"
    write_split(train_path, [True, False])
    write_split(dev_path, [True])
    write_split(test_path, [False, True])

    optimize.optimize_agent_from_project(
        str(project_dir),
        None,
        None,
        str(train_path),
        str(dev_path),
        str(test_path),
        False,
        4,
        2,
        "gpt-4o-mini",
        "fraction",
        None,
        17,
        str(tmp_path / "runs"),
        False,
        False,
    )

    assert env.stop_callback_result is True
    assert any(label == "max" for label, _ in env.stopper_calls)
    assert env.composite_calls


def test_optimize_agent_tracing_studio_init(
    monkeypatch, tmp_path, configure_optimize_env
):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    instructions_file = project_dir / "seed.txt"
    instructions_file.write_text("Seed", encoding="utf-8")

    compiled_path = project_dir / "compiled.yaml"
    compiled_path.write_text(
        yaml.dump(
            {
                "evaluation": {
                    "failure_categories": {"safety": {"measures": ["safety_agent"]}},
                    "judges": {},
                },
                "instructions_file": instructions_file.name,
                "tracing": {
                    "provider": "studio",
                    "name": "workflow",
                    "project": "demo",
                    "settings": {"resume": "auto"},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    env = configure_optimize_env(compiled_path)

    tracing_calls = []
    monkeypatch.setattr(
        "omniagents.core.tracing.local.init",
        lambda **kwargs: tracing_calls.append(kwargs),
    )

    scenario_dir = project_dir / "judge-datasets" / "safety"
    scenario_dir.mkdir(parents=True)

    def write_split(path, labels):
        path.write_text(
            yaml.dump(
                {
                    "scenarios": [
                        {
                            "id": f"{path.stem}-{idx}",
                            "label": lbl,
                            "name": f"scenario-{idx}",
                            "prompt": "prompt",
                            "target_category": "safety",
                        }
                        for idx, lbl in enumerate(labels)
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    write_split(scenario_dir / "train.yml", [True, False])
    write_split(scenario_dir / "dev.yml", [True])
    write_split(scenario_dir / "test.yml", [True])

    result = optimize.optimize_agent_from_project(
        str(project_dir),
        None,
        "safety",
        None,
        None,
        None,
        False,
        4,
        2,
        "gpt-4o-mini",
        "fraction",
        None,
        3,
        None,
        False,
        False,
    )

    assert tracing_calls
    assert tracing_calls[0]["project"] == "demo"
    assert "studio_optimize" in env.adapter_instances[0].kwargs.get("workflow_name", "")
    assert env.engine_kwargs.get("stop_callback") is not None
    assert env.stopper_calls
    assert result["instructions"] == env.instructions


def test_optimize_agent_tracing_failure_continues(
    monkeypatch, tmp_path, configure_optimize_env
):
    """Test that optimization continues when local tracing fails to start."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    instructions_file = project_dir / "seed.txt"
    instructions_file.write_text("Seed", encoding="utf-8")

    compiled_path = project_dir / "compiled.yaml"
    compiled_path.write_text(
        yaml.dump(
            {
                "evaluation": {
                    "failure_categories": {"safety": {"measures": ["safety_agent"]}},
                    "judges": {},
                },
                "instructions_file": instructions_file.name,
                "tracing": {
                    "provider": "studio",
                    "name": "workflow",
                    "project": "demo",
                    "settings": {"resume": "auto"},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    env = configure_optimize_env(compiled_path)

    def failing_init(**kwargs):
        raise RuntimeError("forced")

    monkeypatch.setattr("omniagents.core.tracing.local.init", failing_init)

    scenario_dir = project_dir / "judge-datasets" / "safety"
    scenario_dir.mkdir(parents=True)

    def write_split(path, labels):
        path.write_text(
            yaml.dump(
                {
                    "scenarios": [
                        {
                            "id": f"{path.stem}-{idx}",
                            "label": lbl,
                            "name": f"scenario-{idx}",
                            "prompt": "prompt",
                            "target_category": "safety",
                        }
                        for idx, lbl in enumerate(labels)
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    write_split(scenario_dir / "train.yml", [True, False])
    write_split(scenario_dir / "dev.yml", [True])
    write_split(scenario_dir / "test.yml", [True])

    # Optimization should still work even without tracing
    optimize.optimize_agent_from_project(
        str(project_dir),
        None,
        "safety",
        None,
        None,
        None,
        False,
        4,
        2,
        "gpt-4o-mini",
        "fraction",
        None,
        5,
        None,
        False,
        False,
    )

    # Stopper should still be called even without tracing
    assert env.stopper_calls


def test_optimize_agent_apply_failure(monkeypatch, tmp_path, configure_optimize_env):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    compiled_path = project_dir / "compiled.yaml"
    compiled_path.write_text(
        yaml.dump(
            {
                "evaluation": {
                    "failure_categories": {"safety": {"measures": ["safety_agent"]}},
                    "judges": {},
                },
                "instructions": "Seed",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    env = configure_optimize_env(compiled_path, apply_raises=True)

    def write_split(path, labels):
        path.write_text(
            yaml.dump(
                {
                    "scenarios": [
                        {
                            "id": f"{path.stem}-{idx}",
                            "label": lbl,
                            "name": f"scenario-{idx}",
                            "prompt": "prompt",
                            "target_category": "safety",
                        }
                        for idx, lbl in enumerate(labels)
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    train_path = tmp_path / "train.yml"
    dev_path = tmp_path / "dev.yml"
    test_path = tmp_path / "test.yml"
    write_split(train_path, [True, False])
    write_split(dev_path, [True])
    write_split(test_path, [True])

    result = optimize.optimize_agent_from_project(
        str(project_dir),
        None,
        None,
        str(train_path),
        str(dev_path),
        str(test_path),
        False,
        3,
        2,
        "gpt-4o-mini",
        "fraction",
        None,
        11,
        str(tmp_path / "output"),
        False,
        True,
    )

    assert result["instructions"] == env.instructions
    assert env.apply_attempts
    assert not env.apply_calls
    assert result["applied"] is False
    assert result["apply_error"] == "apply failed"


def test_optimize_agent_apply_true(monkeypatch, tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    compiled_path = project_dir / "compiled.yaml"
    compiled_path.write_text(
        yaml.dump(
            {
                "evaluation": {
                    "failure_categories": {"safety": {"measures": ["safety_agent"]}},
                    "judges": {},
                },
                "instructions": "Start",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    ctx = setup_optimize_stubs(
        monkeypatch,
        compiled_path,
        best_instructions="refined",
    )
    train_path = tmp_path / "train.yml"
    dev_path = tmp_path / "dev.yml"
    test_path = tmp_path / "test.yml"
    create_split(train_path, [True])
    create_split(dev_path, [True])
    create_split(test_path, [True])
    result = optimize.optimize_agent_from_project(
        str(project_dir),
        "agent-main",
        "safety",
        str(train_path),
        str(dev_path),
        str(test_path),
        False,
        5,
        3,
        "gpt-4o-mini",
        "fraction",
        None,
        0,
        str(tmp_path / "output"),
        False,
        True,
    )
    assert result["instructions"] == "refined"
    assert ctx.apply_calls == [
        ((str(project_dir), "agent-main"), {"instructions": "refined"})
    ]


def test_optimize_judge_happy_path(monkeypatch, tmp_path, configure_optimize_env):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    instructions_file = project_dir / "seed.txt"
    instructions_file.write_text("Judge seed", encoding="utf-8")

    compiled_path = project_dir / "compiled.yaml"
    compiled_path.write_text(
        yaml.dump(
            {
                "evaluation": {
                    "failure_categories": {"safety": {"measures": ["safety_agent"]}},
                    "judges": {
                        "safety_judge": {
                            "agent": {"instructions": "Judge base"},
                            "input": {"template": "{{ text }}"},
                        }
                    },
                },
                "tracing": {
                    "provider": "studio",
                    "name": "judgeflow",
                    "project": "demo",
                    "settings": {"resume": "auto"},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    env = configure_optimize_env(
        compiled_path,
        best_candidate={"instructions": "judge", "template": "tmpl"},
        total_num_evals=3,
    )

    judge_adapter_instances = []

    class DummyJudgeAdapter:
        def __init__(self, cfg_path, jkey, conversations, **kwargs):
            self.cfg_path = cfg_path
            self.jkey = jkey
            self.conversations = conversations
            self.kwargs = kwargs
            self.evaluate_calls = []
            judge_adapter_instances.append(self)

        def evaluate(self, dataset, candidate, capture_traces=False):
            self.evaluate_calls.append((dataset, candidate))
            outputs = [{"pred": bool(item.get("label"))} for item in dataset]
            scores = [1.0 for _ in dataset]
            return types.SimpleNamespace(outputs=outputs, scores=scores)

    monkeypatch.setattr(
        "omniagents.core.eval.gepa_judge_adapter.OmniAgentsJudgeAdapter",
        DummyJudgeAdapter,
    )

    judge_apply_calls = []

    class FakeWorkspace:
        def __init__(self, project):
            self.project = project

        def update_judge(self, key, **changes):
            judge_apply_calls.append((self.project, key, changes))

    monkeypatch.setattr("omniagents.core.eval.workspace.EvalWorkspace", FakeWorkspace)

    scenario_dir = project_dir / "judge-datasets" / "safety"
    scenario_dir.mkdir(parents=True)

    def write_split(path, labels):
        path.write_text(
            yaml.dump(
                {
                    "scenarios": [
                        {
                            "id": f"{path.stem}-{idx}",
                            "label": lbl,
                            "name": f"scenario-{idx}",
                            "prompt": "prompt",
                            "target_category": "safety",
                        }
                        for idx, lbl in enumerate(labels)
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    write_split(scenario_dir / "train.yml", [True, False, True])
    write_split(scenario_dir / "dev.yml", [True, False])
    write_split(scenario_dir / "test.yml", [False, True])

    set_dir = project_dir / "generated" / "scenario"
    set_dir.mkdir(parents=True)
    (set_dir / "scenarios.yml").write_text("scenarios: []\n", encoding="utf-8")
    (set_dir / "manifest.json").write_text(
        json.dumps({"timestamp": "2024_01_01_00_00_00", "name": "scenario"}),
        encoding="utf-8",
    )
    (scenario_dir / "manifest.yml").write_text(
        "set: generated/scenario/scenarios.yml\n", encoding="utf-8"
    )
    # The judge reads the conversations of the dataset's set's saved run, not
    # the suite's.
    from omniagents.core.eval.layout import EvalLayout
    from omniagents.core.eval.results import save_evaluation_run

    layout = EvalLayout(project_dir, project_dir)

    ids = [
        f"{split}-{i}"
        for split, n in (("train", 3), ("dev", 2), ("test", 2))
        for i in range(n)
    ]
    save_evaluation_run(
        layout,
        {
            "runs": [
                {
                    "scenario_config": {"id": sid},
                    "final_assistant_text": f"reply {sid}",
                    "history": [{"role": "assistant", "content": f"reply {sid}"}],
                }
                for sid in ids
            ]
        },
        source="test",
        label="scenario",
        scenario_set=set_dir / "scenarios.yml",
    )
    save_evaluation_run(
        layout,
        {"runs": [{"scenario_config": {"id": ids[0]}, "history": []}]},
        source="test",
        label="suite",
    )

    result = optimize.optimize_judge_from_project(
        str(project_dir),
        None,
        "safety",
        None,
        None,
        None,
        "safety_judge",
        None,
        "final_assistant_text",
        2,
        4,
        None,
        "instructions,template",
        None,
        9,
        None,
        False,
        True,
    )

    assert result["judge"] == "safety_judge"
    assert result["instructions"] == "judge"
    assert result["template"] == "tmpl"
    assert judge_apply_calls == [
        (
            str(project_dir),
            "safety_judge",
            {"instructions": "judge", "template": "tmpl"},
        )
    ]
    assert result["applied"] is True
    # Note: tracing initialization is tested separately in test_optimize_judge_tracing
    assert judge_adapter_instances[0].evaluate_calls
    assert set(judge_adapter_instances[0].conversations) == set(ids)
    assert judge_adapter_instances[0].conversations[ids[0]]["history"]
    run_dir = Path(result["run_dir"])
    assert run_dir.parent == project_dir / "optimizations"
    assert run_dir.name.endswith("_judge_safety_judge")
    env.engine_kwargs["evaluator"](
        [{"label": True}],
        {"instructions": "judge", "template": "tmpl"},
    )
    if env.engine_kwargs.get("stop_callback"):
        env.engine_kwargs["stop_callback"](types.SimpleNamespace(total_num_evals=1))


def test_optimize_agent_missing_measures(monkeypatch, tmp_path, configure_optimize_env):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    compiled_path = project_dir / "compiled.yaml"
    compiled_path.write_text(
        yaml.dump(
            {
                "evaluation": {
                    "failure_categories": {"safety": {"measures": []}},
                    "judges": {},
                },
                "instructions": "Seed",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    configure_optimize_env(compiled_path)

    def write_split(path, labels):
        path.write_text(
            yaml.dump(
                {
                    "scenarios": [
                        {
                            "id": f"{path.stem}-{idx}",
                            "label": lbl,
                            "name": f"scenario-{idx}",
                            "prompt": "prompt",
                            "target_category": "safety",
                        }
                        for idx, lbl in enumerate(labels)
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    train_path = tmp_path / "train.yml"
    dev_path = tmp_path / "dev.yml"
    test_path = tmp_path / "test.yml"
    write_split(train_path, [True])
    write_split(dev_path, [True])
    write_split(test_path, [True])

    import omniagents.core.eval.pipeline as pipeline

    monkeypatch.setattr(pipeline, "_failure_category_map", lambda ev: {})

    with pytest.raises(SystemExit) as exc:
        optimize.optimize_agent_from_project(
            str(project_dir),
            None,
            "safety",
            str(train_path),
            str(dev_path),
            str(test_path),
            False,
            5,
            3,
            "gpt-4o-mini",
            "fraction",
            None,
            0,
            None,
            False,
            False,
        )

    assert "Failure category 'safety' has no measures configured" in str(exc.value)


def test_an_optimization_gets_a_new_folder_or_resumes_a_named_one(tmp_path):
    config = types.SimpleNamespace(eval_dir=tmp_path / "evaluations", project_root=tmp_path)

    fresh = optimize._optimization_dir(config, "agent", "safety", None, False)
    assert fresh.parent == tmp_path / "evaluations" / "optimizations"
    assert fresh.name.endswith("_agent_safety") and fresh.is_dir()

    with pytest.raises(SystemExit, match="Name the optimization to resume"):
        optimize._optimization_dir(config, "agent", "safety", None, True)
    with pytest.raises(SystemExit, match="No optimization to resume"):
        optimize._optimization_dir(config, "agent", "safety", "elsewhere", True)

    (fresh / "gepa.log").write_text("started")
    assert optimize._optimization_dir(config, "agent", "safety", str(fresh), True) == fresh
    with pytest.raises(SystemExit, match="resume it with --resume"):
        optimize._optimization_dir(config, "agent", "safety", str(fresh), False)
    named = optimize._optimization_dir(config, "judge", "j", "mine", False)
    assert named == tmp_path / "mine"
