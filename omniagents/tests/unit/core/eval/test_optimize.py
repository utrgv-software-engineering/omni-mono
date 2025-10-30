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
