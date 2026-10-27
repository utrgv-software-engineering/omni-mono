"""The review assistant organizes and proposes; nothing it produces is a
label until a person accepts it."""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path

import pytest
import yaml

from omniagents.core.eval import pipeline
from omniagents.core.eval.judge_validation import is_pinned
from omniagents.core.eval.workspace import EvalWorkspace, EvalWorkspaceError, NotFound


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    _write(tmp_path / "project.yml", "name: demo\nagents:\n  entrypoint: main\n")
    _write(tmp_path / "agents/main/agent.yml", "name: main\ninstructions: Help.\n")
    _write(
        tmp_path / "evaluations/evaluation.yml",
        """\
        evaluation:
          failure_categories:
            - name: Wrong Units
              description: Answers in units the user did not ask for
          synthetic_data_generation:
            dimensions:
              - name: tone
                values: [{name: polite}, {name: blunt}]
              - name: units
                values: [{name: metric}, {name: imperial}, {name: kelvin}]
            generator:
              agent: {model: gpt-4.1-2025-04-14}
              input: {template: "{{ selected_mapping }}"}
        """,
    )
    _write(
        tmp_path / "evaluations/scenarios/main.yml",
        "scenarios:\n  - name: hi\n    prompt: hi\n",
    )
    return tmp_path


def _run(workspace: EvalWorkspace, monkeypatch, n: int) -> list[str]:
    """Save a run with ``n`` answers; returns their conversation ids."""
    records = [
        {
            "scenario": f"s{i}",
            "scenario_config": {
                "id": f"s{i}",
                "name": f"s{i}",
                "prompt": f"weather {i}",
                "dimensions": {"tone": "polite" if i % 2 else "blunt"},
            },
            "final_assistant_text": f"It is {i} degrees"
            + (" kelvin" if i % 3 == 0 else ""),
            "history": [{"role": "user", "content": f"weather {i}"}],
            "passed": i % 4 != 0,
            "trace_group_id": f"g{i}",
        }
        for i in range(n)
    ]
    monkeypatch.setattr(
        pipeline,
        "run_evaluation",
        lambda agent_path, **kw: {
            "runs": records,
            "manifest": {},
            "workflow_name": "wf",
        },
    )
    workspace.run(include_unmeasured=True)
    return [r["trace_group_id"] for r in records]


def test_a_sample_mixes_cluster_representatives_and_random_unreviewed_answers(
    project, monkeypatch
):
    workspace = EvalWorkspace(project)
    groups = _run(workspace, monkeypatch, 30)
    workspace.save_review(groups[0], reviewer="ana", verdict="acceptable")

    sample = workspace.draw_review_sample(size=10, seed=4)

    ids = [item["group_id"] for item in sample["items"]]
    assert len(ids) == len(set(ids)) == 10 and groups[0] not in ids
    reasons = [item["reason"] for item in sample["items"]]
    assert sum(r.startswith("represents cluster") for r in reasons) == 6
    assert sum(r == "random pick" for r in reasons) == 4
    assert workspace.draw_review_sample(size=10, seed=4)["items"] == sample["items"]
    assert workspace.review_assist()["sample"]["items"] == sample["items"]
    # Local working state, beside the runs.
    assert (project / "evaluations/runs/review-assist/sample.json").is_file()


def test_notes_are_grouped_into_proposed_failure_modes(project, monkeypatch):
    workspace = EvalWorkspace(project)
    groups = _run(workspace, monkeypatch, 4)
    workspace.save_review(
        groups[0], reviewer="ana", verdict="unacceptable", notes="kelvin again"
    )
    workspace.save_review(
        groups[1],
        reviewer="ana",
        verdict="unacceptable",
        notes="asked twice for the city",
    )
    seen = {}

    async def ask(instructions, prompt):
        seen["prompt"] = json.loads(prompt)
        return {
            "modes": [
                {
                    "existing_category": "wrong_units",
                    "name": "x",
                    "note_ids": [groups[0], "s-elsewhere"],
                },
                {
                    "existing_category": None,
                    "name": "Asks for a location it was given",
                    "description": "Asks again for the city the user named",
                    "note_ids": [groups[1], "made-up"],
                },
            ]
        }

    proposals = asyncio.run(
        workspace.propose_failure_modes(
            other_notes=[
                {
                    "id": "s-elsewhere",
                    "verdict": "unacceptable",
                    "notes": "kelvin in chat",
                }
            ],
            ask=ask,
        )
    )
    assert {n["id"] for n in seen["prompt"]["notes"]} == {
        groups[0],
        groups[1],
        "s-elsewhere",
    }
    assert seen["prompt"]["existing_categories"][0]["id"] == "wrong_units"
    existing, new = proposals
    assert existing["existing_category"] == "wrong_units"
    assert new["review_ids"] == [groups[1]]  # an id it invented is dropped

    # Nothing is labelled until a proposal is accepted.
    assert not workspace.review(groups[0]).get("failure_categories")
    accepted = workspace.accept_proposal(existing["id"], reviewer="bo")
    assert accepted["unapplied"] == ["s-elsewhere"]  # labelled by the caller
    assert workspace.review(groups[0])["failure_categories"] == ["wrong_units"]
    accepted = workspace.accept_proposal(
        new["id"], reviewer="bo", name="Re-asks the city"
    )
    assert accepted["category"]["name"] == "Re-asks the city"
    assert workspace.review(groups[1])["failure_categories"] == ["re-asks_the_city"]
    assert workspace.review_assist()["proposals"] == []
    with pytest.raises(NotFound):
        workspace.dismiss_proposal(new["id"])

    workspace.remove_review(groups[0])
    workspace.remove_review(groups[1])
    with pytest.raises(EvalWorkspaceError, match="no review notes"):
        asyncio.run(workspace.propose_failure_modes(ask=ask))


def test_labels_are_suggested_for_a_failure_mode_and_applied_once_accepted(
    project, monkeypatch
):
    workspace = EvalWorkspace(project)
    groups = _run(workspace, monkeypatch, 10)
    workspace.save_review(
        groups[0],
        reviewer="ana",
        verdict="unacceptable",
        failure_categories=["wrong_units"],
        notes="kelvin",
    )
    batches = []

    async def ask(instructions, prompt):
        data = json.loads(prompt)
        batches.append(data)
        return {
            "matches": [
                {"id": a["id"], "quote": "kelvin", "reason": "answers in kelvin"}
                for a in data["answers"]
                if "kelvin" in a["reply"]
            ]
        }

    added = asyncio.run(workspace.suggest_labels("Wrong Units", ask=ask))

    # Every answer but the one already labelled was read, in batches, beside
    # the labelled example.
    assert sum(len(b["answers"]) for b in batches) == 9
    assert batches[0]["labelled_examples"][0]["reviewer_note"] == "kelvin"
    assert sorted(s["group_id"] for s in added) == ["g3", "g6", "g9"]
    assert not workspace.review("g3")  # not a label yet

    by_group = {s["group_id"]: s for s in added}
    workspace.decide_suggestions([by_group["g3"]["id"]], accept=True, reviewer="bo")
    workspace.decide_suggestions([by_group["g6"]["id"]], accept=False, reviewer="bo")
    review = workspace.review("g3")
    assert review["failure_categories"] == ["wrong_units"]
    assert review["verdict"] == "unacceptable"
    assert not workspace.review("g6")

    # A dismissed suggestion is not made again; pending ones are not repeated.
    assert asyncio.run(workspace.suggest_labels("wrong_units", ask=ask)) == []


def _dataset_with_notes(workspace, monkeypatch, n=24):
    groups = _run(workspace, monkeypatch, n)
    for i, group in enumerate(groups):
        failing = i % 2 == 0
        workspace.save_review(
            group,
            reviewer="ana",
            verdict="unacceptable" if failing else "acceptable",
            failure_categories=["wrong_units"] if failing else [],
            notes=f"note {i}" if i % 3 else None,
        )
    workspace.add_judge(
        "units_judge", model="gpt-4.1-2025-04-14", failure_category="Wrong Units"
    )
    return workspace.create_judge_dataset("wrong_units", seed=1)


def _split_ids(project, split):
    data = yaml.safe_load(
        (project / f"evaluations/judge-datasets/wrong_units/{split}.yml").read_text()
    )
    return data["scenarios"]


def test_a_judge_takes_its_few_shot_examples_from_the_train_split(project, monkeypatch):
    workspace = EvalWorkspace(project)
    _dataset_with_notes(workspace, monkeypatch)
    train = _split_ids(project, "train")
    with_notes = [e for e in train if e.get("notes")]
    without = [e for e in train if not e.get("notes")]
    dev = _split_ids(project, "dev")
    before = (project / "evaluations/judges/units_judge/judge.yml").read_text()

    with pytest.raises(EvalWorkspaceError, match="dev or test split"):
        workspace.set_judge_examples("units_judge", [dev[0]["id"]])
    if without:
        with pytest.raises(EvalWorkspaceError, match="no reviewer notes"):
            workspace.set_judge_examples("units_judge", [without[0]["id"]])
    assert (project / "evaluations/judges/units_judge/judge.yml").read_text() == before

    chosen = [with_notes[0]["id"]]
    judge = workspace.set_judge_examples("units_judge", chosen)
    assert judge["examples"] == chosen

    from omniagents.core.eval.judge_validation import judge_version
    from omniagents.core.eval.loader import judge_examples_text

    cfg = workspace.config().config["judges"]["units_judge"]
    text = judge_examples_text(cfg)
    expected = "Fail" if with_notes[0]["label"] else "Pass"
    assert text.startswith("## Examples\n\n### Example 1: " + expected)
    assert f"Reasoning: {with_notes[0]['notes']}" in text
    assert text.endswith(f"Answer: {expected}")
    # The examples are part of the judge's version.
    workspace.set_judge_examples("units_judge", [])
    assert judge_version(
        workspace.config().config["judges"]["units_judge"]
    ) != judge_version(cfg)


def test_judge_models_are_pinned_to_dated_snapshots():
    assert is_pinned("gpt-4.1-2025-04-14") and is_pinned("claude-sonnet-4-5-20250929")
    assert not is_pinned("gpt-4.1") and not is_pinned(None)


def test_scenarios_are_generated_from_reviewed_combinations_and_filtered_for_realism(
    project, monkeypatch
):
    from omniagents.core.eval import review_assist, scenarios

    workspace = EvalWorkspace(project)
    with pytest.raises(NotFound, match="No combinations"):
        asyncio.run(workspace.generate_scenarios("batch"))

    draft = workspace.draft_combinations(
        "batch", count=4, seed=2, dimension_values={"units": ["metric", "kelvin"]}
    )
    assert len(draft["combinations"]) == 4
    assert {c["units"] for c in draft["combinations"]} <= {"metric", "kelvin"}
    assert [d["name"] for d in workspace.drafts()] == ["batch"]
    with pytest.raises(EvalWorkspaceError, match="no value"):
        workspace.set_combinations("batch", [{"tone": "polite", "units": "parsecs"}])
    with pytest.raises(EvalWorkspaceError, match="one value of each"):
        workspace.set_combinations("batch", [{"tone": "polite"}])
    reviewed = [
        {"tone": "polite", "units": "metric"},
        {"tone": "blunt", "units": "kelvin"},
    ]
    workspace.set_combinations("batch", reviewed)

    async def fake_build(base_dir, agent_cfg, dims_catalog=None, raw_dims=None):
        return object()

    async def fake_generation(agent, prompt, max_turns=1):
        return [{"name": "q", "prompt": f"query for {prompt}"}]

    async def rate(instructions, prompt):
        queries = json.loads(prompt)["queries"]
        return {
            "ratings": [
                {
                    "id": q["id"],
                    "score": 1 if q["user"]["units"] == "kelvin" else 5,
                    "reason": "r",
                }
                for q in queries
            ]
        }

    monkeypatch.setattr(scenarios, "_build_generator_agent", fake_build)
    monkeypatch.setattr(scenarios, "_run_generation", fake_generation)
    monkeypatch.setattr(review_assist, "model_asker", lambda model: rate)

    generated = asyncio.run(workspace.generate_scenarios("batch", count=2))

    assert [s["dimensions"] for s in generated["scenarios"]] == [reviewed[0]]
    folder = project / "evaluations/generated/batch"
    filtered = yaml.safe_load((folder / "filtered.yml").read_text())["scenarios"]
    assert (
        filtered[0]["dimensions"] == reviewed[1]
        and filtered[0]["realism"]["score"] == 1
    )
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["counts"] == {"total": 1, "filtered": 1}
    assert workspace.drafts() == []
    with pytest.raises(EvalWorkspaceError, match="already generated"):
        workspace.set_combinations("batch", reviewed)
