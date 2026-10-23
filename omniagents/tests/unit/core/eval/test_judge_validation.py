"""A judge is trusted once it is measured against people's labels, once, on
its dataset's test split; a run then reports its verdicts corrected for its
errors."""

from pathlib import Path

import pytest

from omniagents.core.eval import judge_validation as jv
from omniagents.core.eval.layout import EvalLayout


def test_pass_is_the_positive_class():
    counts = jv.confusion(
        [(True, True)] * 9
        + [(True, False)]
        + [(False, False)] * 3
        + [(False, True)]
        + [(True, None)]
    )
    assert (counts["tp"], counts["fn"], counts["tn"], counts["fp"]) == (9, 1, 3, 1)
    assert counts["unanswered"] == 1
    assert counts["tpr"] == 0.9 and counts["tnr"] == 0.75


def test_the_corrected_rate_is_rogan_gladen():
    # validate-evaluator's worked example: 80% observed, TPR .92, TNR .88.
    assert jv.corrected_rate(0.80, 0.92, 0.88) == pytest.approx(0.85)
    assert jv.corrected_rate(0.99, 0.6, 0.6) == 1.0  # clipped
    assert jv.corrected_rate(0.5, 0.52, 0.5) is None  # no better than chance


def test_the_interval_is_seeded_and_brackets_the_estimate():
    test = {"tp": 46, "fn": 4, "tn": 44, "fp": 6}
    first = jv.corrected_interval(test, passed=160, judged=200)
    assert first == jv.corrected_interval(test, passed=160, judged=200)
    estimate = jv.corrected_rate(0.8, 46 / 50, 44 / 50)
    assert first[0] < estimate < first[1]
    assert first[1] - first[0] < 0.25


def _judge(root: Path, instructions: str) -> dict:
    folder = root / "judges" / "tone"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "instructions.md").write_text(instructions)
    return {
        "agent": {"model": "m", "instructions_file": str(folder / "instructions.md")},
        "failure_category": "rude",
    }


def test_a_version_is_measured_on_the_test_split_once(tmp_path):
    layout = EvalLayout(tmp_path, tmp_path)
    test_split = tmp_path / "judge-datasets" / "rude" / "test.yml"
    test_split.parent.mkdir(parents=True)
    test_split.write_text("scenarios: []\n")
    assert jv.is_test_split(layout, test_split)
    assert not jv.is_test_split(layout, test_split.with_name("dev.yml"))

    cfg = _judge(tmp_path, "Fail rude replies.")
    assert jv.validation_status(layout, "tone", cfg)["status"] == "unvalidated"
    jv.check_test_unspent(layout, "tone", jv.judge_version(cfg), test_split)
    counts = jv.confusion([(True, True), (False, False)])
    jv.record_validation(layout, "tone", cfg, test_split, counts, source="judge run")

    assert jv.validation_status(layout, "tone", cfg)["status"] == "validated"
    with pytest.raises(jv.TestSplitSpent, match="measured once per version"):
        jv.check_test_unspent(layout, "tone", jv.judge_version(cfg), test_split)

    # Editing the judge makes a new version: unvalidated, its test unspent.
    edited = _judge(tmp_path, "Fail rude or curt replies.")
    assert jv.validation_status(layout, "tone", edited)["status"] == "changed"
    jv.check_test_unspent(layout, "tone", jv.judge_version(edited), test_split)
    # So does a test split with more answers in it.
    test_split.write_text("scenarios: [{id: a}]\n")
    jv.check_test_unspent(layout, "tone", jv.judge_version(cfg), test_split)


def test_a_run_reports_each_judge_raw_and_corrected(tmp_path):
    layout = EvalLayout(tmp_path, tmp_path)
    test_split = tmp_path / "judge-datasets" / "rude" / "test.yml"
    test_split.parent.mkdir(parents=True)
    test_split.write_text("scenarios: []\n")
    validated = _judge(tmp_path, "Fail rude replies.")
    pairs = [(True, True)] * 46 + [(True, False)] * 4 + [(False, False)] * 44
    pairs += [(False, True)] * 6
    jv.record_validation(
        layout, "tone", validated, test_split, jv.confusion(pairs), source="judge run"
    )
    runs = [
        {"metadata": {"tone": {"answer": "Pass"}, "other": {"answer": "Fail"}}}
    ] * 8 + [
        {"metadata": {"tone": {"answer": "Fail"}, "other": {"answer": "Pass"}}}
    ] * 2
    runs += [{"metadata": {"tone": {"error": "judge_failed"}}}]

    report = {
        r["judge"]: r
        for r in jv.judge_report(
            layout, {"tone": validated, "other": {"agent": {}}}, runs
        )
    }

    tone = report["tone"]
    assert tone["status"] == "validated" and tone["answered"] == 10
    assert tone["raw_pass_rate"] == 0.8
    assert tone["corrected_pass_rate"] == pytest.approx(
        jv.corrected_rate(0.8, 46 / 50, 44 / 50)
    )
    assert tone["corrected_interval"][0] < tone["corrected_pass_rate"]
    other = report["other"]
    assert other["status"] == "unvalidated" and other["raw_pass_rate"] == 0.2
    assert other["corrected_pass_rate"] is None
