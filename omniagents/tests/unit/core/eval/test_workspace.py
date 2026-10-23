"""The evaluation workspace edits a project's evaluation files in place and
saves, reads and compares runs the same way for every caller."""

from __future__ import annotations

import json
import textwrap
import uuid
from pathlib import Path

import pytest
import yaml

from omniagents.core.eval import pipeline
from omniagents.core.eval.layout import SPLIT_NAMES
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

    workspace.add_failure_category(
        "Ignores Location", description="Answers for elsewhere"
    )
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
    assert (
        workspace.update_measure("cites_source", description="Links it")["description"]
        == "Links it"
    )
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


def test_generated_scenarios_are_promoted_and_removed(project):
    workspace = EvalWorkspace(project)
    generated = str(project / "evaluations/generated/batch1/scenarios.yml")

    workspace.promote_scenario(generated, scenario_id="g2")

    promoted = workspace.scenario_set(project / "evaluations/scenarios/promoted.yml")
    assert [s["id"] for s in promoted["scenarios"]] == ["g2"]

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
                "trace_group_id": f"s{index}-{uuid.uuid4().hex[:8]}",
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


def test_a_set_run_is_saved_in_runs(project, monkeypatch):
    workspace = EvalWorkspace(project)
    generated = project / "evaluations/generated/batch1/scenarios.yml"
    seen = {}

    def fake_run(agent_path, **kwargs):
        seen.update(kwargs)
        return _engine_result([True, True, False, True])

    monkeypatch.setattr(pipeline, "run_evaluation", fake_run)

    saved = workspace.run(scenario_set=generated)
    assert [s["id"] for s in seen["eval_cfg"]["scenarios"]] == ["g1", "g2", "g3", "g4"]
    assert seen["include_unmeasured"] is True
    assert Path(saved["path"]).parent.parent == project / "evaluations/runs"
    assert saved["metadata"]["set"] == "generated/batch1/scenarios.yml"


def _saved_run(workspace, monkeypatch, passes, **result):
    monkeypatch.setattr(
        pipeline,
        "run_evaluation",
        lambda agent_path, **kwargs: {**_engine_result(passes), **result},
    )
    return workspace.run()


def test_reviews_are_kept_with_the_answers_they_judge(project, monkeypatch):
    workspace = EvalWorkspace(project)
    run = _saved_run(workspace, monkeypatch, [False, True])
    first, second = run["runs"]
    with pytest.raises(EvalWorkspaceError, match="No saved run"):
        workspace.save_review("not-a-run", reviewer="ana", verdict="acceptable")
    with pytest.raises(EvalWorkspaceError, match="Unknown failure category"):
        workspace.save_review(
            first["trace_group_id"], reviewer="ana", failure_categories=["nope"]
        )

    review = workspace.save_review(
        first["trace_group_id"],
        reviewer="ana",
        verdict="unacceptable",
        failure_categories=["Wrong Units"],
        notes="kelvin again",
    )
    assert review["failure_categories"] == ["wrong_units"]
    assert review["reply"] == "reply 0" and review["scenario"]["id"] == "s0"
    assert "history" not in review  # no judge of the project reads conversations
    path = project / "evaluations/reviews" / f"{run['metadata']['id']}.jsonl"
    [line] = path.read_text().splitlines()
    assert json.loads(line)["verdict"] == "unacceptable"

    # Fields not given keep what the review had.
    workspace.save_review(first["trace_group_id"], reviewer="bo", notes="")
    kept = workspace.review(first["trace_group_id"])
    assert kept["verdict"] == "unacceptable" and kept["notes"] is None
    assert kept["reviewer"] == "bo"

    # The review stands on its own once the run is gone.
    import shutil

    shutil.rmtree(Path(run["path"]).parent)
    assert workspace.is_reviewable(first["trace_group_id"])
    assert not workspace.is_reviewable(second["trace_group_id"])

    # A review left empty is removed, and so is a file with none left.
    workspace.save_review(
        first["trace_group_id"], reviewer="bo", verdict=None, failure_categories=[]
    )
    assert workspace.reviews() == [] and not path.exists()


def test_a_review_keeps_the_conversation_when_a_judge_reads_one(project, monkeypatch):
    workspace = EvalWorkspace(project)
    workspace.add_judge("units_judge", model="gpt-5.5", source="history")
    run = _saved_run(workspace, monkeypatch, [False])
    group = run["runs"][0]["trace_group_id"]
    review = workspace.save_review(group, reviewer="ana", verdict="unacceptable")
    assert review["history"] == [{"role": "assistant", "content": "reply 0"}]


def test_judge_datasets_are_built_from_reviews_and_keep_their_splits(
    project, monkeypatch
):
    workspace = EvalWorkspace(project)
    run = _saved_run(workspace, monkeypatch, [False] * 12)
    groups = [record["trace_group_id"] for record in run["runs"]]
    for index, group in enumerate(groups[:8]):
        workspace.save_review(
            group,
            reviewer="ana",
            verdict="unacceptable" if index % 2 else "acceptable",
            failure_categories=["wrong_units"] if index % 2 else [],
        )
    workspace.save_review(groups[8], reviewer="ana", notes="only a note")

    with pytest.raises(NotFound):
        workspace.create_judge_dataset("not_a_category")
    dataset = workspace.create_judge_dataset("wrong_units", seed=7)
    folder = project / "evaluations/judge-datasets/wrong_units"
    manifest = yaml.safe_load((folder / "manifest.yml").read_text())
    assert manifest["source"] == "reviews" and manifest["seed"] == 7
    # Eight reviewed answers; a note alone is not a verdict.
    assert sum(s["scenario_count"] for s in dataset["splits"]) == 8
    assert sum(s["positives"] for s in dataset["splits"]) == 4

    def assignment():
        out = {}
        for split in SPLIT_NAMES:
            for example in yaml.safe_load((folder / f"{split}.yml").read_text())[
                "scenarios"
            ]:
                out[example["id"]] = split
        return out

    before = assignment()
    split = workspace.judge_dataset_split(dataset["splits"][1]["path"])
    for example in split["scenarios"]:
        assert example["reply"].startswith("reply")
        assert example["label"] is (
            "wrong_units" in workspace.review(example["id"])["failure_categories"]
        )

    # More reviews add examples and move none of the earlier ones.
    for group in groups[9:]:
        workspace.save_review(group, reviewer="bo", verdict="acceptable")
    workspace.create_judge_dataset("wrong_units", seed=7)
    after = assignment()
    assert len(after) == 11
    assert all(after[key] == split for key, split in before.items())

    workspace.remove_judge_dataset("wrong_units")
    assert workspace.judge_datasets() == [] and not folder.exists()


def test_a_judge_dataset_built_from_scenario_labels_is_refused(project):
    folder = project / "evaluations/judge-datasets/wrong_units"
    _write(folder / "manifest.yml", "set: generated/batch1/scenarios.yml\n")
    with pytest.raises(EvalWorkspaceError, match="built from scenario labels"):
        EvalWorkspace(project).judge_datasets()


def test_a_run_is_kept_as_a_committed_baseline(project, monkeypatch):
    workspace = EvalWorkspace(project)
    run = _saved_run(workspace, monkeypatch, [True, False])

    kept = workspace.keep_run(run["path"], "before-rewrite")
    folder = project / "evaluations/baselines/before-rewrite"
    assert kept["baseline"] == "before-rewrite"
    assert (folder / "results.json").is_file() and (
        folder / "agent/agent.yml"
    ).is_file()
    with pytest.raises(EvalWorkspaceError, match="exists"):
        workspace.keep_run(run["path"], "before-rewrite")
    with pytest.raises(EvalWorkspaceError, match="letters, digits"):
        workspace.keep_run(run["path"], "no spaces")
    with pytest.raises(EvalWorkspaceError, match="Only a saved run"):
        workspace.keep_run(kept["path"], "again")

    # Listed and compared with the runs, and still there once the run is not.
    import shutil

    shutil.rmtree(Path(run["path"]).parent)
    later = _saved_run(workspace, monkeypatch, [True, True])
    listed = {entry["path"]: entry["baseline"] for entry in workspace.runs()}
    assert listed == {kept["path"]: "before-rewrite", later["path"]: None}
    assert workspace.compare_runs(kept["path"], later["path"])["comparable"]

    # A review of a baseline's answer is filed under the original run id.
    group = workspace.read_run(kept["path"])["runs"][1]["trace_group_id"]
    workspace.save_review(group, reviewer="ana", verdict="unacceptable")
    assert (
        project / "evaluations/reviews" / f"{run['metadata']['id']}.jsonl"
    ).is_file()

    workspace.remove_baseline("before-rewrite")
    assert not folder.exists()


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
    train = yaml.safe_load((project / "evaluations/holdout/train.yml").read_text())[
        "scenarios"
    ]
    workspace.run(split="train", include_unmeasured=True)
    assert split_used["split"] == "train"
    assert len(split_used["names"]) == len(train)


def test_a_judge_defined_by_reference_edits_the_referenced_files(project):
    shared = project / "evaluations/shared"
    _write(
        shared / "tone_agent.yml",
        "agent:\n  model: gpt-5.5\n  instructions_file: tone.md\n",
    )
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
