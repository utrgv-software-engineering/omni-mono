import builtins
import sys
import types

import pytest

from omniagents.core.eval import optimize


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
        """
evaluation:
  failure_categories:
    safety:
      measures:
        - safety_agent
  metrics: []
        """.strip(),
        encoding="utf-8",
    )

    def fake_compile_project(*args, **kwargs):
        return str(compiled_path), {}

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

    compiler_module = types.ModuleType("omniagents.core.project.compiler")
    compiler_module.compile_project = fake_compile_project
    compiler_module.__package__ = "omniagents.core.project"
    monkeypatch.setitem(
        sys.modules,
        "omniagents.core.project.compiler",
        compiler_module,
    )

    apply_module = types.ModuleType("omniagents.core.project.apply")
    apply_module.apply_agent_instructions = lambda *args, **kwargs: None
    apply_module.__package__ = "omniagents.core.project"
    monkeypatch.setitem(
        sys.modules,
        "omniagents.core.project.apply",
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

    assert "Could not locate scenario splits for category 'safety'" in str(exc.value)


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


def test_optimize_agent_happy_path(monkeypatch, tmp_path):
    import yaml

    adapter_instances = []

    class DummyAdapter:
        def __init__(self, cfg_path, category, **kwargs):
            self.cfg_path = cfg_path
            self.category = category
            self.kwargs = kwargs
            self.evaluate_calls = []
            adapter_instances.append(self)

        def evaluate(self, dataset, candidate, capture_traces=False):
            self.evaluate_calls.append((dataset, candidate))
            scores = [0.8 for _ in dataset]
            return types.SimpleNamespace(outputs=dataset, scores=scores)

    monkeypatch.setattr(
        "omniagents.core.eval.gepa_agent_adapter.OmniAgentsAgentAdapter",
        DummyAdapter,
    )

    def fake_compile_project(*args, **kwargs):
        return str(compiled_path), {}

    monkeypatch.setattr(
        "omniagents.core.project.compiler.compile_project", fake_compile_project
    )

    def fake_apply(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "omniagents.core.project.apply.apply_agent_instructions", fake_apply
    )

    import sys

    dummy_gepa = types.ModuleType("gepa")
    dummy_gepa.__path__ = []
    monkeypatch.setitem(sys.modules, "gepa", dummy_gepa)

    core_pkg = types.ModuleType("gepa.core")
    core_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "gepa.core", core_pkg)

    class DummyEngine:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run(self):
            return types.SimpleNamespace(
                total_num_evals=3,
                best_candidate={"instructions": "optimized"},
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

    class DummyStopper:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def __call__(self, state):
            return False

    class DummyCompositeStopper:
        def __init__(self, *stoppers):
            self.stoppers = stoppers

        def __call__(self, state):
            return any(stopper(state) for stopper in self.stoppers)

    utils_mod.FileStopper = DummyStopper
    utils_mod.MaxMetricCallsStopper = DummyStopper
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

    def write_split(path, labels):
        data = {
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
        }
        path.write_text(yaml.dump(data, sort_keys=False), encoding="utf-8")

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
        None,
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
    assert result["instructions"] == "optimized"
    assert adapter_instances[0].evaluate_calls
    assert any(msg.startswith("LOG_PATH:") for msg in logs)
