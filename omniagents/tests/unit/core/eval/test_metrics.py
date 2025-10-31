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
