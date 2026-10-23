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
    _write(tmp_path / "evaluations/scenarios/main.yml", SCENARIOS_YML)
    generated = tmp_path / "evaluations/generated/batch1"
    _write(
        generated / "scenarios.yml",
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
    (generated / "manifest.json").write_text(json.dumps({"name": "batch1"}))
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
    scenarios = (project / "evaluations/scenarios/main.yml").read_text()
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
    project_set = str(project / "evaluations/scenarios/main.yml")

    kinds = [(s["kind"], s["label"]) for s in workspace.scenario_sets()]
    assert kinds == [("project", "main.yml"), ("generated", "batch1")]
    assert workspace.resolve_set("batch1") == (
        project / "evaluations/generated/batch1/scenarios.yml"
    )

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

    text = (project / "evaluations/scenarios/main.yml").read_text()
    assert "# Regression from the March outage." in text
    names = [s["name"] for s in workspace.scenario_set(project_set)["scenarios"]]
    assert names == ["celsius", "storm"]
    with pytest.raises(EvalWorkspaceError, match="outside the project"):
        workspace.scenario_set("/etc/passwd")


def test_generated_scenarios_are_promoted_labelled_and_removed(project):
    workspace = EvalWorkspace(project)
    generated = str(project / "evaluations/generated/batch1/scenarios.yml")

    workspace.promote_scenario(generated, scenario_id="g2")
    workspace.label_scenario(
        generated,
        scenario_id="g4",
        failure_categories=["Wrong Units"],
        review={"judgment": "fail", "notes": "kelvin again"},
    )

    promoted = workspace.scenario_set(project / "evaluations/scenarios/promoted.yml")
    assert [s["id"] for s in promoted["scenarios"]] == ["g2"]
    labelled = workspace.scenario_set(generated)["scenarios"][3]
    assert labelled["failure_categories"] == ["wrong_units"]
    assert labelled["analysis"] == {"judgment": "fail", "notes": "kelvin again"}

    workspace.remove_scenario_set(generated)
    assert not (project / "evaluations/generated/batch1").exists()


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
    assert judge["defined_in"].endswith("judges/units_judge/judge.yml")
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
    assert first["metadata"]["set"] is None and first["metadata"]["label"] == "baseline"
    run_dir = Path(first["path"]).parent
    assert run_dir.parent == project / "evaluations/runs"
    # The run keeps the agent it tested.
    assert (run_dir / "agent/agent.yml").read_text() == (
        project / "agents/main/agent.yml"
    ).read_text()
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


def test_a_set_run_is_saved_in_runs_and_feeds_its_judge_dataset(
    project, monkeypatch
):
    workspace = EvalWorkspace(project)
    generated = project / "evaluations/generated/batch1/scenarios.yml"
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
    assert Path(saved["path"]).parent.parent == project / "evaluations/runs"
    assert saved["metadata"]["set"] == "generated/batch1/scenarios.yml"
    # A suite run does not stand in for the set's conversations.
    workspace.run(name="suite")

    dataset = workspace.create_judge_dataset(
        "evaluations/generated/batch1/scenarios.yml",
        "wrong_units",
        fractions=(0.5, 0.25, 0.25),
        seed=1,
    )
    folder = project / "evaluations/judge-datasets/wrong_units"
    assert dataset["category"] == "wrong_units" and dataset["set"] == "batch1"
    assert yaml.safe_load((folder / "manifest.yml").read_text())["set"] == (
        "generated/batch1/scenarios.yml"
    )
    assert sum(s["positives"] for s in dataset["splits"]) == 2
    split = workspace.judge_dataset_split(dataset["splits"][0]["path"])
    assert split["scenarios"] and all(
        s["reply"].startswith("reply") for s in split["scenarios"]
    )

    workspace.remove_judge_dataset("wrong_units")
    assert workspace.judge_datasets() == [] and not folder.exists()


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
        (project / "evaluations/holdout/train.yml").read_text()
    )["scenarios"]
    workspace.run(split="train", include_unmeasured=True)
    assert split_used["split"] == "train"
    assert len(split_used["names"]) == len(train)


def test_a_judge_defined_by_reference_edits_the_referenced_files(project):
    shared = project / "evaluations/shared"
    _write(shared / "tone_agent.yml", "agent:\n  model: gpt-5.5\n  instructions_file: tone.md\n")
    _write(shared / "tone.md", "Judge the tone.")
    _write(
        project / "evaluations/judges/tone/judge.yml",
        "agent_ref: ../../shared/tone_agent.yml\n",
    )
    workspace = EvalWorkspace(project)

    assert workspace.judge("tone")["instructions"] == "Judge the tone."
    workspace.update_judge("tone", instructions="Judge the tone strictly.")

    assert (shared / "tone.md").read_text() == "Judge the tone strictly."


def test_an_old_layout_is_refused_as_a_workspace_error(project):
    (project / "artifacts/eval/old").mkdir(parents=True)
    workspace = EvalWorkspace(project)
    with pytest.raises(EvalWorkspaceError, match="artifacts/eval"):
        workspace.scenario_sets()
    with pytest.raises(EvalWorkspaceError, match="laid out the old way"):
        workspace.config()


def test_labels_come_from_the_reviews_of_the_sets_newest_run(project, monkeypatch):
    from omniagents.core.eval import annotate

    workspace = EvalWorkspace(project)
    generated = project / "evaluations/generated/batch1/scenarios.yml"
    with pytest.raises(EvalWorkspaceError, match="no saved run"):
        workspace.annotate_from_reviews(generated, "traces")

    def fake_run(agent_path, **kwargs):
        return {**_engine_result([True]), "workflow_name": f"wf{len(read)}"}

    monkeypatch.setattr(pipeline, "run_evaluation", fake_run)
    read = []
    workspace.run(scenario_set=generated)
    read.append("older")
    workspace.run(scenario_set=generated)
    read.append("newest")
    workspace.run()  # wf2: newer, but the suite's run is not the set's

    def labels(project_name, workflow_name):
        read.append((project_name, workflow_name))
        return {"g2": ["wrong_units"]}

    monkeypatch.setattr(annotate, "review_labels", labels)
    result = workspace.annotate_from_reviews(generated, "traces")

    assert read[-1] == ("traces", "wf1")
    by_id = {s["id"]: s for s in result["set"]["scenarios"]}
    assert by_id["g2"]["failure_categories"] == ["wrong_units"]
    assert "failure_categories" not in by_id["g1"]  # no review, no label
    assert not list(generated.parent.glob("*.bak*"))
