"""Failure reasons on AI graders: `fail_reasons:` in judge.yml."""

from omniagents.core.eval.pipeline import normalize_fail_reason
from omniagents.core.evaluation.context import EvalContext

CFG = {"fail_reasons": ["none", "missing_request", "unrequested_scope"]}


def test_fail_keeps_a_listed_reason():
    out = normalize_fail_reason(
        {"answer": "Fail", "reasoning": "r", "reason": "missing_request"}, CFG
    )
    assert out["reason"] == "missing_request"


def test_fail_with_an_unlisted_reason_is_unspecified():
    out = normalize_fail_reason(
        {"answer": "Fail", "reasoning": "r", "reason": "too_slow"}, CFG
    )
    assert (out["reason"], out["reason_given"]) == ("unspecified", "too_slow")


def test_fail_without_a_reason_is_unspecified():
    out = normalize_fail_reason({"answer": "Fail", "reasoning": "r"}, CFG)
    assert out["reason"] == "unspecified" and "reason_given" not in out


def test_none_is_not_a_failure_reason():
    out = normalize_fail_reason(
        {"answer": "Fail", "reasoning": "r", "reason": "none"}, CFG
    )
    assert out["reason"] == "unspecified"


def test_pass_carries_no_reason():
    out = normalize_fail_reason(
        {"answer": "Pass", "reasoning": "r", "reason": "none"}, CFG
    )
    assert "reason" not in out


def test_judges_without_fail_reasons_are_unchanged():
    out = {"answer": "Fail", "reasoning": "r", "reason": "anything"}
    assert normalize_fail_reason(out, {}) is out


def _ctx(output):
    return EvalContext(metadata={"task_compliance": output})


def test_judge_result_fails_with_the_reason_and_keeps_the_reasoning():
    judge = _ctx(
        {
            "answer": "Fail",
            "reasoning": "Docs not updated.",
            "reason": "missing_request",
        }
    ).judge_result("task_compliance")
    assert judge.reason == "missing_request"
    assert judge.to_result() == {
        "passed": False,
        "reason": "missing_request",
        "reasoning": "Docs not updated.",
    }


def test_judge_result_without_a_reason_keeps_todays_shape():
    judge = _ctx({"answer": "Fail", "reasoning": "Docs not updated."}).judge_result(
        "task_compliance"
    )
    assert judge.reason is None
    assert judge.to_result() == {"passed": False, "reason": "Docs not updated."}


def test_judge_result_pass_is_unchanged():
    judge = _ctx({"answer": "Pass", "reasoning": "Fine."}).judge_result(
        "task_compliance"
    )
    assert judge.to_result() == {"passed": True, "reason": "Fine."}
