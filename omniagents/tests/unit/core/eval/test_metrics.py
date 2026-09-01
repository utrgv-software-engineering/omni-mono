import pytest

from omniagents.core.eval.metrics import compute_metrics


def test_compute_metrics_handles_pass_and_rate_targets():
    runs = [
        {
            "measures": [
                {
                    "name": "grade",
                    "passed": True,
                    "details": {"score": 0.9, "max": 1.0},
                },
                {
                    "name": "grade",
                    "passed": False,
                    "details": {"score": 0.4, "max": 1.0},
                },
                {"name": "speed", "passed": True, "details": {"value": 3}},
            ]
        },
        {
            "measures": [
                {
                    "name": "grade",
                    "passed": True,
                    "details": {"score": 0.8, "max": 1.0},
                },
                {
                    "name": "grade",
                    "passed": True,
                    "details": {"score": 0.7, "max": 1.0},
                },
            ]
        },
    ]

    metrics_cfg = [
        {
            "type": "pass_rate",
            "name": "grade_pass",
            "source_measure": "grade",
            "target": {"op": ">=", "value": 0.75},
        },
        {
            "type": "pass_rate",
            "name": "invalid_target",
            "source_measure": "grade",
            "target": {"op": ">=", "value": "not-a-number"},
        },
        {
            "type": "rate",
            "name": "grade_score",
            "source_measure": "grade",
            "numerator": "details.score",
            "denominator": "details.max",
            "target": {"op": ">", "value": 0.85},
        },
        {"type": "mystery", "name": "unknown"},
    ]

    results = compute_metrics(runs, metrics_cfg)

    grade_pass = results[0]
    assert grade_pass["value"] == pytest.approx(0.75, rel=1e-6)
    assert grade_pass["meets_target"] is True
    assert grade_pass["passed_runs"] == 3
    assert grade_pass["runs"] == 4

    invalid = results[1]
    assert invalid["meets_target"] is False
    assert invalid["value"] == 0.75

    rate_metric = results[2]
    assert rate_metric["value"] == pytest.approx(0.7, rel=1e-6)
    assert rate_metric["meets_target"] is False
    assert rate_metric["numerator_sum"] == pytest.approx(2.8)
    assert rate_metric["denominator_sum"] == pytest.approx(4.0)

    unknown = results[3]
    assert unknown["value"] is None
    assert unknown["reason"] == "unknown_metric_type:mystery"


def _run(name, tone, tags, passed, answered=True):
    return {
        "scenario": name,
        "answered": answered,
        "final_assistant_text": "x" if answered else "",
        "scenario_config": {"name": name, "tags": tags, "expect": {"skin_tone": tone}},
        "measures": [{"name": "top1", "passed": passed}],
    }


def test_pass_rate_carries_a_wilson_interval():
    runs = [_run("a", "12", [], True), _run("b", "12", [], False), _run("c", "56", [], True), _run("d", "56", [], True)]
    (m,) = compute_metrics(runs, [{"name": "top1_rate", "type": "pass_rate", "source_measure": "top1"}])
    assert m["value"] == 0.75
    low, high = m["interval"]
    assert 0.0 < low < 0.75 < high <= 1.0


def test_pass_rate_slices_by_scenario_fields_including_tags():
    runs = [
        _run("a", "12", ["dev", "benign"], True),
        _run("b", "12", ["dev", "malignant"], False),
        _run("c", "56", ["dev", "benign"], True),
        _run("d", "56", ["test", "malignant"], True),
    ]
    (m,) = compute_metrics(
        runs,
        [{"name": "top1_rate", "type": "pass_rate", "source_measure": "top1", "slice_by": ["expect.skin_tone", "tags"]}],
    )
    tone = m["slices"]["expect.skin_tone"]
    assert tone["12"]["runs"] == 2 and tone["12"]["passed_runs"] == 1
    assert tone["56"]["runs"] == 2 and tone["56"]["passed_runs"] == 2
    assert m["slices"]["tags"]["dev"]["runs"] == 3
    assert m["slices"]["tags"]["malignant"]["passed_runs"] == 1
    assert "interval" in tone["12"]


def test_default_slice_by_applies_to_every_metric():
    runs = [_run("a", "12", [], True), _run("b", "56", [], False)]
    (m,) = compute_metrics(
        runs, [{"name": "r", "type": "pass_rate", "source_measure": "top1"}], default_slice_by="expect.skin_tone"
    )
    assert set(m["slices"]["expect.skin_tone"]) == {"12", "56"}


def test_nonresponses_count_as_failures_unless_excluded_and_answered_rate_reports_them():
    runs = [_run("a", "12", [], True), _run("b", "12", [], False, answered=False), _run("c", "56", [], True)]
    counted, excluded, answered = compute_metrics(
        runs,
        [
            {"name": "counted", "type": "pass_rate", "source_measure": "top1"},
            {"name": "excluded", "type": "pass_rate", "source_measure": "top1", "exclude_nonresponse": True},
            {"name": "answered", "type": "answered_rate", "slice_by": ["expect.skin_tone"], "target": {"op": ">=", "value": 0.9}},
        ],
    )
    assert counted["runs"] == 3 and counted["passed_runs"] == 2
    assert excluded["runs"] == 2 and excluded["passed_runs"] == 2 and excluded["excluded_nonresponses"] == 1
    assert answered["value"] == pytest.approx(2 / 3) and answered["non_responses"] == 1
    assert answered["slices"]["expect.skin_tone"]["12"]["passed_runs"] == 1
    assert answered["meets_target"] is False
