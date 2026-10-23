"""The evaluation workspace edits a project's evaluation files in place and
saves, reads and compares runs the same way for every caller."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
import yaml

from omniagents.core.eval import pipeline
from omniagents.core.eval.workspace import EvalWorkspace, EvalWorkspaceError, NotFound

EVALUATION_YML = """\
evaluation:
  # Kept short while the agent changes.
  defaults:
    max_turns: 4
  failure_categories:
    - name: Wrong Units
      measures: [expectations]
"""

SCENARIOS_YML = """\
scenarios:
  # Regression from the March outage.
  - id: s1
    name: celsius
    prompt: Weather in Paris in celsius
    failure_categories: [wrong_units]
  - name: hello
    prompt: Say hello
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    _write(tmp_path / "project.yml", "name: demo\nagents:\n  entrypoint: main\n")
    _write(
        tmp_path / "agents/main/agent.yml",
        "name: main\nmodel: gpt-5.5\ninstructions: Help.\n",
    )
    _write(tmp_path / "evaluations/evaluation.yml", EVALUATION_YML)
    _write(tmp_path / "evaluations/scenarios.yml", SCENARIOS_YML)
    generated = tmp_path / "artifacts/eval/batch1"
    _write(
        generated / "generated_scenarios.yml",
        """\
        scenarios:
          - id: g1
            name: kelvin
            prompt: Paris in kelvin?
            failure_categories: [wrong_units]
          - id: g2
            name: fahrenheit
            prompt: Paris in fahrenheit
          - id: g3
            name: rankine
            prompt: Paris in rankine
            failure_categories: [wrong_units]
          - id: g4
            name: plain
            prompt: Paris weather
        """,
    )
    (generated / "manifest.json").write_text(json.dumps({"name": "Batch one"}))
    return tmp_path


def test_dimensions_are_written_into_evaluation_yml_in_place(project):
    workspace = EvalWorkspace(project)

    workspace.set_dimensions(
        [
            {
                "name": "units",
                "values": [{"name": "metric", "examples": ["celsius", " "]}],
            }
        ]
    )

    assert workspace.dimensions() == [
        {
            "name": "units",
            "label": None,
            "description": None,
            "values": [
                {
                    "name": "metric",
                    "label": None,
                    "description": None,
                    "examples": ["celsius"],
                }
            ],
        }
    ]
    text = (project / "evaluations/evaluation.yml").read_text()
    assert "# Kept short while the agent changes." in text
    with pytest.raises(EvalWorkspaceError, match="twice"):
        workspace.set_dimensions([{"name": "a", "values": []}, {"name": "a"}])


def test_failure_categories_and_their_scenarios(project):
    workspace = EvalWorkspace(project)

    [units] = workspace.failure_categories()
    assert units["id"] == "wrong_units"
    assert units["scenario_count"] == 3  # one project, two generated

    workspace.add_failure_category("Ignores Location", description="Answers for elsewhere")
    with pytest.raises(EvalWorkspaceError, match="Unknown measure"):
        workspace.add_failure_category("Other", measures=["no_such_measure"])
    with pytest.raises(EvalWorkspaceError, match="exists"):
        workspace.add_failure_category("ignores location")
    updated = workspace.update_failure_category(
        "ignores_location", measures=["user_goal_achieved"]
    )
    assert updated["measures"] == ["user_goal_achieved"]

    assert workspace.remove_failure_category("wrong_units") == 3
    assert [c["id"] for c in workspace.failure_categories()] == ["ignores_location"]
    scenarios = (project / "evaluations/scenarios.yml").read_text()
    assert "failure_categories" not in scenarios
    assert "# Regression from the March outage." in scenarios


def test_measures_list_builtin_and_project_functions(project):
    workspace = EvalWorkspace(project)

    names = {m["name"]: m for m in workspace.measures()}
    assert names["expectations"]["builtin"] is True
    assert names["expectations"]["usage_count"] == 1  # the category lists it

    added = workspace.add_measure("cites_source", label="Cites a source")
    assert added == {
        **added,
        "builtin": False,
        "implemented": True,
        "label": "Cites a source",
    }
    assert "def cites_source(ctx):" in (project / "evaluations/measures.py").read_text()
    assert workspace.update_measure("cites_source", description="Links it")[
        "description"
    ] == "Links it"
    workspace.remove_measure("cites_source")
    # The function is code and stays; only its description is gone.
    assert workspace._measure("cites_source")["label"] == "Cites Source"
    with pytest.raises(EvalWorkspaceError, match="identifier"):
        workspace.add_measure("not valid")


def test_scenarios_are_edited_in_place_and_stale_edits_refused(project):
    workspace = EvalWorkspace(project)
    project_set = str(project / "evaluations/scenarios.yml")

    kinds = [(s["kind"], s["label"]) for s in workspace.scenario_sets()]
    assert kinds == [("project", "scenarios.yml"), ("generated", "Batch one")]

    workspace.add_scenario(project_set, {"name": "storm", "prompt": "Any storms?"})
    with pytest.raises(EvalWorkspaceError, match="in this set"):
        workspace.add_scenario(project_set, {"name": "storm", "prompt": "again"})
    workspace.update_scenario(
        project_set,
        {"id": "s1", "name": "celsius", "prompt": "Paris, celsius please"},
        scenario_id="s1",
    )
    with pytest.raises(NotFound):
        workspace.remove_scenario(project_set, scenario_id="gone")
    workspace.remove_scenario(project_set, name="hello")

    text = (project / "evaluations/scenarios.yml").read_text()
    assert "# Regression from the March outage." in text
    names = [s["name"] for s in workspace.scenario_set(project_set)["scenarios"]]
    assert names == ["celsius", "storm"]
    with pytest.raises(EvalWorkspaceError, match="outside the project"):
        workspace.scenario_set("/etc/passwd")


def test_generated_scenarios_are_promoted_labelled_and_removed(project):
    workspace = EvalWorkspace(project)
    generated = str(project / "artifacts/eval/batch1/generated_scenarios.yml")

    workspace.promote_scenario(generated, scenario_id="g2")
    workspace.label_scenario(
        generated,
        scenario_id="g4",
        failure_categories=["Wrong Units"],
        review={"judgment": "fail", "notes": "kelvin again"},
    )

    promoted = workspace.scenario_set(project / "evaluations/scenarios.yml")
    assert promoted["scenarios"][-1]["id"] == "g2"
    labelled = workspace.scenario_set(generated)["scenarios"][3]
    assert labelled["failure_categories"] == ["wrong_units"]
    assert labelled["analysis"] == {"judgment": "fail", "notes": "kelvin again"}

    workspace.remove_scenario_set(generated)
    assert not (project / "artifacts/eval/batch1").exists()


def test_judges_are_created_edited_and_removed(project):
    workspace = EvalWorkspace(project)

    judge = workspace.add_judge(
        "units_judge",
        model="gpt-5.5",
        failure_category="Wrong Units",
        instructions="Fail answers in the wrong units.",
    )
    assert judge["failure_category"] == "Wrong Units"
    assert judge["instructions"] == "Fail answers in the wrong units."
    assert judge["instructions_path"].endswith("judges/units_judge/instructions.md")
    units = workspace._category("wrong_units")
    assert units["measures"] == ["expectations", "units_judge_agent"]

    assert workspace.update_judge("units_judge", template="{{ text }}")["template"] == (
        "{{ text }}"
    )
    workspace.remove_judge("units_judge")
    assert workspace.judges() == []
    assert not (project / "evaluations/judges/units_judge").exists()


def _engine_result(passes: list[bool], *, select=None, model="gpt-5.5"):
    runs = []
    for index, passed in enumerate(passes):
        runs.append(
            {
                "scenario": f"s{index}",
                "scenario_config": {"id": f"s{index}", "name": f"scenario {index}"},
                "measures": [{"name": "expectations", "passed": passed}],
                "answered": True,
                "final_assistant_text": f"reply {index}",
                "history": [{"role": "assistant", "content": f"reply {index}"}],
            }
        )
    return {
        "runs": runs,
        "metrics": [],
        "stats": {"total_scenarios": len(passes)},
        "manifest": {"model": model, "select": select, "scenarios": len(passes)},
        "workflow_name": None,
    }


def test_runs_are_saved_whole_listed_and_compared(project, monkeypatch):
    workspace = EvalWorkspace(project)
    results = iter(
        [
            _engine_result([True, False]),
            _engine_result([True, True], model="gpt-6"),
            _engine_result([True], select="name:x"),
        ]
    )
    calls = []

    def fake_run(agent_path, **kwargs):
        calls.append(kwargs)
        return next(results)

    monkeypatch.setattr(pipeline, "run_evaluation", fake_run)

    first = workspace.run(name="baseline")
    second = workspace.run()
    third = workspace.run(select="name:celsius")

    assert calls[0]["include_history"] is True
    assert first["overview"]["pass_rate"] == 0.5
    assert first["overview"]["interval"][0] < 0.5 < first["overview"]["interval"][1]
    assert first["manifest"]["model"] == "gpt-5.5"
    assert first["metadata"]["agent"] == "main"
    assert Path(first["path"]).parent.parent == project / "evaluations/results"
    assert [r["path"] for r in workspace.runs()] == [
        third["path"],
        second["path"],
        first["path"],
    ]

    comparison = workspace.compare_runs(first["path"], second["path"])
    assert comparison["comparable"] is True
    assert comparison["differences"] == {"model": ["gpt-5.5", "gpt-6"]}
    assert comparison["pass_rate"]["change"] == 0.5
    assert comparison["scenarios"] == [
        {"scenario": "s1", "name": "scenario 1", "a": 0.0, "b": 1.0}
    ]
    refused = workspace.compare_runs(first["path"], third["path"])
    assert refused["comparable"] is False
    assert "select" in refused["reason"]
    assert refused["pass_rate"]["change"] is None


def test_a_set_run_is_saved_beside_the_set_and_feeds_its_judge_datasets(
    project, monkeypatch
):
    workspace = EvalWorkspace(project)
    generated = project / "artifacts/eval/batch1/generated_scenarios.yml"
    seen = {}

    def fake_run(agent_path, **kwargs):
        seen.update(kwargs)
        data = _engine_result([True, True, False, True])
        for run, scenario in zip(data["runs"], kwargs["eval_cfg"]["scenarios"]):
            run["scenario_config"] = scenario
        return data

    monkeypatch.setattr(pipeline, "run_evaluation", fake_run)

    saved = workspace.run(scenario_set=generated)
    assert [s["id"] for s in seen["eval_cfg"]["scenarios"]] == ["g1", "g2", "g3", "g4"]
    assert seen["include_unmeasured"] is True
    assert Path(saved["path"]).parent.parent == generated.parent / "results"

    dataset = workspace.create_judge_dataset(
        generated, "wrong_units", fractions=(0.5, 0.25, 0.25), seed=1
    )
    assert sum(s["positives"] for s in dataset["splits"]) == 2
    split = workspace.judge_dataset_split(dataset["splits"][0]["path"])
    assert all(s["reply"].startswith("reply") for s in split["scenarios"])

    workspace.remove_judge_dataset("batch1", "wrong_units")
    assert workspace.judge_datasets() == []


def test_holdout_is_created_and_selects_the_suite(project, monkeypatch):
    workspace = EvalWorkspace(project)

    holdout = workspace.create_holdout(fractions=(0.5, 0.5, 0.0), seed=3)
    assert holdout["total"] == 2
    assert workspace.holdout()["counts"] == holdout["counts"]

    split_used = {}

    def fake_run(agent_path, **kwargs):
        split_used["split"] = kwargs["eval_config"].split
        split_used["names"] = [s["name"] for s in kwargs["eval_cfg"]["scenarios"]]
        return _engine_result([True])

    monkeypatch.setattr(pipeline, "run_evaluation", fake_run)
    train = yaml.safe_load(
        (project / "evaluations/splits/eval/train.yml").read_text()
    )["scenarios"]
    workspace.run(split="train", include_unmeasured=True)
    assert split_used["split"] == "train"
    assert len(split_used["names"]) == len(train)


def test_a_judge_defined_by_reference_edits_the_referenced_files(project):
    shared = project / "evaluations/shared"
    _write(shared / "tone_agent.yml", "agent:\n  model: gpt-5.5\n  instructions_file: tone.md\n")
    _write(shared / "tone.md", "Judge the tone.")
    _write(
        project / "evaluations/evaluation.yml",
        EVALUATION_YML + "  judges:\n    tone:\n      agent_ref: shared/tone_agent.yml\n",
    )
    workspace = EvalWorkspace(project)

    assert workspace.judge("tone")["instructions"] == "Judge the tone."
    workspace.update_judge("tone", instructions="Judge the tone strictly.")

    assert (shared / "tone.md").read_text() == "Judge the tone strictly."
