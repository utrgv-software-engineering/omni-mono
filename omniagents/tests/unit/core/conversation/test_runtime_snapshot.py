from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from omniagents.core.conversation.runtime_snapshot import (
    reconcile_runtime_controls,
    snapshot_state_events,
)


def test_display_state_survives_acknowledgement_but_run_status_requires_live_run():
    session = SimpleNamespace(
        id="owner",
        current_task=Mock(done=lambda: False),
        active_session_state_payload={"function": "ui.set_session_state", "args": {}},
        active_run_status_payload={"status": "running"},
        active_run_set_status_payload={
            "function": "ui.set_status",
            "args": {"text": "Working"},
        },
    )
    events = snapshot_state_events(session)
    assert len(events) == 3
    assert all(event["params"]["session_id"] == "owner" for event in events)
    assert "session_id" not in session.active_run_status_payload
    session.current_task = None
    assert snapshot_state_events(session) == events[:1]


@pytest.mark.parametrize(
    "kind,content,field",
    [
        ("approval", {"call_id": "a"}, "pending_tool_approvals"),
        (
            "approval",
            {"approval_kind": "mcp", "request_id": "a"},
            "pending_mcp_approvals",
        ),
        ("elicitation", {"elicitation_id": "a"}, "pending_elicitations"),
    ],
)
def test_only_live_runtime_controls_are_actionable(kind, content, field):
    item = {"kind": kind, "status": "started", "content": content}
    session = SimpleNamespace()
    live = SimpleNamespace(**{"_" + field: {"a": (Mock(done=lambda: False), session)}})
    assert reconcile_runtime_controls([item], session, live) == [item]
    assert (
        reconcile_runtime_controls([item], SimpleNamespace(), live)[0]["status"]
        == "failed"
    )
    restored = reconcile_runtime_controls([item], session, SimpleNamespace())[0]
    assert restored["status"] == "failed"
    assert restored["content"]["finalized_by"] == "runtime_reconciliation"
    assert item["status"] == "started"  # no mutation of shared persisted history
    assert reconcile_runtime_controls([restored], session, SimpleNamespace()) == [
        restored
    ]


def test_durable_items_and_resolved_decisions_survive_restart():
    items = [
        {"kind": "plan", "status": "started", "content": {}},
        {"kind": "artifact", "status": "completed", "content": {}},
        {"kind": "approval", "status": "completed", "content": {"decision": "approve"}},
    ]
    assert (
        reconcile_runtime_controls(items, SimpleNamespace(), SimpleNamespace()) == items
    )


@pytest.mark.parametrize(
    "kind,content,field,details",
    [
        (
            "approval",
            {"call_id": "a"},
            "pending_tool_approvals",
            {
                "metadata": {"diff": "+new"},
                "server_label": "server",
                "tool_label": "edit",
            },
        ),
        (
            "elicitation",
            {"elicitation_id": "a"},
            "pending_elicitations",
            {"expires_at": "2026-09-07T12:00:00Z", "persist_response": False},
        ),
    ],
)
def test_live_prompt_details_survive_snapshot_without_changing_history(
    kind, content, field, details
):
    item = {"kind": kind, "status": "started", "content": content}
    session = SimpleNamespace(**{field: {"a": details}})
    runtime = SimpleNamespace(
        **{"_" + field: {"a": (Mock(done=lambda: False), session)}}
    )
    restored = reconcile_runtime_controls([item], session, runtime)[0]
    assert restored["content"] == {**content, **details}
    assert item["content"] == content
    assert reconcile_runtime_controls([item], session, None)[0]["status"] == "failed"


def test_interrupted_tool_is_uncertain_without_rewriting_shared_history():
    item = {
        "kind": "tool_call",
        "status": "started",
        "turn_id": "run",
        "content": {"execution_state": "claimed"},
    }
    session = SimpleNamespace(
        active_run_id="run", current_task=Mock(done=lambda: False)
    )
    assert reconcile_runtime_controls([item], session, None) == [item]
    restored = reconcile_runtime_controls([item], SimpleNamespace(), None)[0]
    assert restored["status"] == "failed"
    assert restored["content"]["execution_state"] == "uncertain"
    assert "Do not assume" in restored["content"]["output"]
    assert item["status"] == "started"
