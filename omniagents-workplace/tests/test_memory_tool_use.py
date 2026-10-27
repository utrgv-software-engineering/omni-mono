"""Known passing/failing traces for the factory's memory tool-use measure."""

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from omniagents.core.eval.environment import EnvironmentContext
from omniagents.core.evaluation.context import EvalContext, ToolCall, ToolOutput

FACTORY = Path(__file__).resolve().parents[3] / "omni-factory"


@pytest.fixture(scope="module")
def measure():
    path = FACTORY / "evaluations/measures.py"
    if not path.exists():
        pytest.skip("sibling factory checkout required")
    spec = importlib.util.spec_from_file_location("factory_measures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.memory_tool_use


def context(operation, args, *, result=None, expected=None):
    return EvalContext(
        metadata={
            "scenario": {
                "expect": {
                    "memory": {
                        "operation": operation,
                        **({"pr_status": "merged"} if operation == "forget" else {}),
                        **(expected or {}),
                    }
                }
            }
        },
        _environment_context=EnvironmentContext(
            session_id="test",
            data={"memory_fixture": {"id": "note-1", "store": "agent:app/implementer"}},
        ),
        _tool_calls=[
            ToolCall(
                index=2,
                name="memory",
                args={"operation": operation, "arguments_json": json.dumps(args)},
                output=ToolOutput(
                    index=3,
                    output_json=(
                        ([{"id": "note-1"}] if operation == "search" else {})
                        if result is None
                        else result
                    ),
                ),
            )
        ],
    )


@pytest.mark.parametrize(
    "operation,args",
    [
        ("search", {"query": "Lyra"}),
        ("remember", {"store": "agent", "text": "fact"}),
        ("update", {"id": "note-1", "text": "fact"}),
        ("supersede", {"old_ids": ["note-1"], "text": "fact"}),
    ],
)
def test_valid_operations(measure, operation, args):
    assert measure(context(operation, args))["passed"]


@pytest.mark.parametrize(
    "operation,args",
    [
        ("search", {"query": None}),
        ("remember", {"store": "team", "text": "fact"}),
        ("update", {"id": "wrong", "text": "fact"}),
        ("supersede", {"old_ids": ["wrong"], "text": "fact"}),
        ("forget", {"id": "note-1", "reason": "done"}),
    ],
)
def test_wrong_arguments(measure, operation, args):
    assert not measure(context(operation, args))["passed"]


def test_wrong_text_and_failed_tool_and_missing_call(measure):
    assert not measure(
        context(
            "remember", {"store": "agent", "text": "wrong"}, expected={"text": "right"}
        )
    )["passed"]
    ctx = context("search", {"query": "Lyra"}, result={"error": "access denied"})
    assert not measure(ctx)["passed"]
    ctx._tool_calls.clear()
    assert not measure(ctx)["passed"]


def test_rejected_forget_is_diagnostic_when_memory_is_retained(measure):
    ctx = context("search", {"query": "lesson"})
    ctx._tool_calls.extend(
        context("forget", {"id": "note-1"}, result={"error": "denied"})._tool_calls
    )
    result = measure(ctx)
    assert result["passed"]
    assert result["diagnostics"] == [{"operation": "forget", "outcome": "rejected"}]


def test_retirement_requires_inspecting_pr_first(measure):
    ctx = context(
        "forget", {"id": "note-1", "reason": "Promoted in acme/api-service#34"}
    )
    assert not measure(ctx)["passed"]
    ctx._tool_calls.insert(
        0,
        ToolCall(
            index=0,
            name="pull_request_read",
            args={
                "method": "get",
                "owner": "acme",
                "repo": "api-service",
                "pullNumber": 34,
            },
            output=ToolOutput(index=1, output_json={"state": "closed", "merged": True}),
        ),
    )
    assert measure(ctx)["passed"]
    ctx._tool_calls[0].index = 4
    assert not measure(ctx)["passed"]


def test_factory_memory_cases_use_the_standard_measure():
    path = FACTORY / "evaluations/scenarios/memory.yml"
    if not path.exists():
        pytest.skip("sibling factory checkout required")
    scenarios = yaml.safe_load(path.read_text())["scenarios"]
    assert {s["name"] for s in scenarios} == {
        "memory_retire_pr_open",
        "memory_retire_pr_merged",
        "memory_retire_pr_closed",
        "memory_improver_daily_promotion",
    }
    assert all(s["agent"] == "improver" for s in scenarios)
    assert all(s["measures"] == ["memory_tool_use"] for s in scenarios)


def pr_read(state="closed", merged=True, *, result=None):
    return ToolCall(
        index=0,
        name="pull_request_read",
        args={
            "method": "get",
            "owner": "acme",
            "repo": "api-service",
            "pullNumber": 34,
        },
        output=ToolOutput(
            index=1,
            output_json=(
                {"state": state, "merged": merged} if result is None else result
            ),
        ),
    )


@pytest.mark.parametrize(
    "status,state,merged",
    [("open", "open", False), ("closed", "closed", False), ("merged", "closed", True)],
)
def test_all_retirement_cases_require_actual_status(measure, status, state, merged):
    ctx = context(
        "forget" if merged else "search",
        (
            {"id": "note-1", "reason": "https://github.com/acme/api-service/pull/34"}
            if merged
            else {"query": "lesson"}
        ),
        expected={"pr_status": status},
    )
    assert not measure(ctx)["passed"]
    read = pr_read(state, merged)
    ctx._tool_calls.insert(0, read)
    assert measure(ctx)["passed"]
    for wrong in (
        {"error": "permission denied"},
        {"state": state},
        {"state": state, "merged": not merged},
    ):
        read.output.output_json = wrong
        assert not measure(ctx)["passed"]


def test_merge_result_must_arrive_before_forget(measure):
    ctx = context("forget", {"id": "note-1", "reason": "acme/api-service#34"})
    read = pr_read()
    read.output.index = 4
    ctx._tool_calls.insert(0, read)
    assert not measure(ctx)["passed"]


def test_mcp_text_result_is_unwrapped_but_errors_are_not(measure):
    ctx = context("forget", {"id": "note-1", "reason": "acme/api-service#34"})
    read = pr_read(
        result={
            "content": [{"type": "text", "text": '{"state":"closed","merged":true}'}]
        }
    )
    ctx._tool_calls.insert(0, read)
    assert measure(ctx)["passed"]
    read.output.output_json["isError"] = True
    assert not measure(ctx)["passed"]


def test_search_must_find_the_seeded_memory(measure):
    for result in ([], [{"id": "unrelated"}], {"message": "ok"}):
        assert not measure(context("search", {"query": "Lyra"}, result=result))[
            "passed"
        ]
    ctx = context("search", {"query": "unrelated"}, result=[])
    ctx._tool_calls.extend(context("search", {"query": "Lyra"})._tool_calls)
    assert measure(ctx)["passed"]


def promotion_context():
    ctx = context(
        "search",
        {"query": "validation"},
        expected={"promotion": {"source_runs": [401, 408, 415]}},
    )
    ctx._tool_calls.append(
        ToolCall(
            index=4,
            name="create_pull_request",
            args={"owner": "acme", "repo": "factory", "head": "improve/2026-10-25"},
            output=ToolOutput(
                index=5,
                output_json={
                    "id": "1",
                    "url": "https://github.com/acme/factory/pull/1",
                },
            ),
        )
    )
    ctx._environment_context.data["github"] = {
        "repositories": [
            {
                "repository": "acme/factory",
                "items": [
                    {
                        "kind": "pull_request",
                        "number": 1,
                        "head": "improve/2026-10-25",
                        "base": "main",
                        "state": "open",
                        "merged": False,
                        "draft": False,
                        "body": "Promotes note-1, from runs 401, 408, and 415.",
                        "files": [
                            "agents/implementer/instructions.md",
                            "evaluations/scenarios/validation.yml",
                        ],
                    }
                ],
            }
        ]
    }
    return ctx


def test_promotion_requires_evidence_and_retains_memory(measure):
    ctx = promotion_context()
    assert measure(ctx)["passed"]
    ctx._tool_calls.extend(
        context("forget", {"id": "note-1", "reason": "promoted"})._tool_calls
    )
    assert not measure(ctx)["passed"]


@pytest.mark.parametrize(
    "change",
    [
        "missing_memory_id",
        "missing_old_run",
        "no_role_change",
        "no_eval",
        "merged",
        "wrong_repo",
        "missing_pr_call",
        "failed_pr_call",
        "pr_before_search",
    ],
)
def test_incomplete_promotion_fails(measure, change):
    ctx = promotion_context()
    repo = ctx._environment_context.data["github"]["repositories"][0]
    pr = repo["items"][0]
    if change == "missing_memory_id":
        pr["body"] = pr["body"].replace("note-1", "")
    elif change == "missing_old_run":
        pr["body"] = pr["body"].replace("401", "")
    elif change == "no_role_change":
        pr["files"].remove("agents/implementer/instructions.md")
    elif change == "no_eval":
        pr["files"].remove("evaluations/scenarios/validation.yml")
    elif change == "merged":
        pr[change] = True
    elif change == "wrong_repo":
        repo["repository"] = "acme/api-service"
    elif change == "missing_pr_call":
        ctx._tool_calls.pop()
    elif change == "failed_pr_call":
        ctx._tool_calls[-1].output.output_json = {"error": "denied"}
    elif change == "pr_before_search":
        ctx._tool_calls[-1].index = 1
    assert not measure(ctx)["passed"]


@pytest.mark.parametrize("wrapper", ["single", "list", "content"])
def test_real_github_text_output_shapes(measure, wrapper):
    text = {"type": "text", "text": '{"state":"closed","merged":true}'}
    result = (
        text
        if wrapper == "single"
        else [text] if wrapper == "list" else {"content": [text]}
    )
    ctx = context("forget", {"id": "note-1", "reason": "acme/api-service#34"})
    ctx._tool_calls.insert(0, pr_read(result=result))
    assert measure(ctx)["passed"]


@pytest.mark.parametrize(
    "operation,result",
    [
        ("search", [{"id": "note-1"}]),
        ("get", {"id": "note-1"}),
        ("core", [{"id": "note-1"}]),
    ],
)
def test_retention_accepts_valid_reads_of_preloaded_memory(measure, operation, result):
    ctx = context(
        operation,
        {"id": "note-1", "query": "lesson"},
        result=result,
        expected={"operation": "read", "pr_status": "open"},
    )
    ctx._tool_calls.insert(0, pr_read("open", False))
    assert measure(ctx)["passed"]
    ctx._tool_calls[-1].output.output_json = (
        {"id": "wrong"} if operation == "get" else [{"id": "wrong"}]
    )
    assert not measure(ctx)["passed"]


def test_sdk_history_input_text_result(measure):
    from omniagents.core.evaluation.context import build_eval_context

    ctx = context("forget", {"id": "note-1", "reason": "acme/api-service#34"})
    history = [
        {
            "type": "function_call",
            "name": "mcp_github__pull_request_read",
            "call_id": "pr",
            "arguments": json.dumps(
                {
                    "method": "get",
                    "owner": "acme",
                    "repo": "api-service",
                    "pullNumber": 34,
                }
            ),
        },
        {
            "type": "function_call_output",
            "call_id": "pr",
            "output": [
                {"type": "input_text", "text": '{"state":"closed","merged":true}'}
            ],
        },
    ]
    ctx._tool_calls.insert(0, build_eval_context(history=history).tool_calls()[0])
    assert measure(ctx)["passed"]


@pytest.mark.parametrize(
    "store,passed",
    [
        ("agent", True),
        ("team", True),
        ("agent:app/improver", True),
        ("agent:app/implementer", False),
    ],
)
def test_promotion_can_record_linkage_in_authorized_store(measure, store, passed):
    ctx = promotion_context()
    ctx._tool_calls.extend(
        context("remember", {"store": store, "text": "Promotion PR link"})._tool_calls
    )
    assert measure(ctx)["passed"] is passed


@pytest.mark.parametrize(
    "error",
    [
        {"error": "invalid source"},
        {"isError": True, "content": [{"type": "text", "text": "denied"}]},
    ],
)
def test_remember_retry_passes_after_rejection(measure, error):
    ctx = context(
        "remember",
        {"store": "team", "text": "wrong"},
        result=error,
        expected={"text": "fact"},
    )
    assert not measure(ctx)["passed"]  # Rejection alone never satisfies the request.
    ctx._tool_calls.extend(
        context("remember", {"store": "agent", "text": "fact"})._tool_calls
    )
    result = measure(ctx)
    assert result["passed"]
    assert result["diagnostics"] == [{"operation": "remember", "outcome": "rejected"}]


def test_blocked_cross_role_update_does_not_fail_completed_promotion(measure):
    ctx = promotion_context()
    ctx._tool_calls.extend(
        context(
            "update",
            {"id": "note-1", "text": "new"},
            result={"error": "no write access"},
        )._tool_calls
    )
    result = measure(ctx)
    assert result["passed"]
    assert result["diagnostics"] == [{"operation": "update", "outcome": "rejected"}]
    ctx._environment_context.data["github"]["repositories"][0]["items"][0][
        "draft"
    ] = True
    result = measure(ctx)
    assert result["passed"]
    assert result["diagnostics"]


@pytest.mark.parametrize("operation", ["forget", "update"])
def test_successful_forbidden_changes_still_fail(measure, operation):
    ctx = promotion_context()
    ctx._tool_calls.extend(
        context(
            operation, {"id": "note-1", "text": "changed", "reason": "premature"}
        )._tool_calls
    )
    assert not measure(ctx)["passed"]


def test_missing_result_is_unknown_not_a_rejected_call(measure):
    ctx = promotion_context()
    call = context("forget", {"id": "note-1"})._tool_calls[0]
    call.output = None
    ctx._tool_calls.append(call)
    result = measure(ctx)
    assert not result["passed"]
    assert "could not be verified" in result["reason"]
    assert not result["diagnostics"]


@pytest.mark.parametrize("draft", [True, False])
def test_promotion_readiness_is_outside_memory_eval(measure, draft):
    ctx = promotion_context()
    pr = ctx._environment_context.data["github"]["repositories"][0]["items"][0]
    pr["draft"] = draft
    assert measure(ctx)["passed"]
    pr["body"] = "Missing memory and source references"
    assert not measure(ctx)["passed"]
