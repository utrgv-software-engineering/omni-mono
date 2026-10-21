"""Approval identities and atomic decisions, independent of model ID quality."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from omniagents.core.agents.service import AgentService


def service():
    svc = object.__new__(AgentService)
    svc._pending_tool_approvals = {}
    svc._pending_mcp_approvals = {}
    svc._resolved_approvals = {}
    svc._resolve_approval_without_client = AsyncMock(return_value=None)
    svc._is_mcp_tool_always_allowed = lambda *args: False
    svc._broadcast_session = AsyncMock()
    svc._record_conversation = Mock()
    svc._persist_approval_decision = Mock()
    return svc


def session(id):
    return SimpleNamespace(
        id=id, user_id=id, pending_tool_approvals={}, pending_mcp_approvals={}
    )


async def request(svc, sess, kind):
    args = {"session": sess, "tool_name": "example", "arguments": "{}"}
    args.update(
        {"call_id": "provider-reused"}
        if kind == "tool"
        else {"request_id": "provider-reused", "server_label": "example"}
    )
    task = asyncio.create_task(getattr(svc, "request_" + kind + "_approval")(**args))
    await asyncio.sleep(0)
    token = svc._broadcast_session.call_args.args[2][
        "call_id" if kind == "tool" else "request_id"
    ]
    return task, token


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["tool", "mcp"])
async def test_colliding_provider_ids_have_distinct_owners_and_waiters(kind):
    svc = service()
    a, b = session("A"), session("B")
    ta, aid = await request(svc, a, kind)
    tb, bid = await request(svc, b, kind)
    assert aid != bid and aid != "provider-reused"
    field = "call_id" if kind == "tool" else "request_id"
    response = getattr(svc, kind + "_approval_response")
    assert (
        svc._resolve_rpc_resource_owner(kind + "_approval_response", {field: aid})[0]
        == "A"
    )
    assert await response(aid, "approve") is True
    assert (await ta)["decision"] == "approve"
    assert not tb.done()
    assert await response(bid, "reject") is True
    assert (await tb)["decision"] == "reject"
    # Retry receipt still has the original owner after pending-map cleanup.
    assert (
        svc._resolve_rpc_resource_owner(kind + "_approval_response", {field: aid})[0]
        == "A"
    )
    assert await response(aid, "approve") is True
    assert not getattr(svc, "_pending_" + kind + "_approvals")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["tool", "mcp"])
async def test_first_decision_wins_permissions_and_recording(kind):
    svc = service()
    sess = session("A")
    task, token = await request(svc, sess, kind)
    response = getattr(svc, kind + "_approval_response")
    assert await response(token, "reject") is True
    assert await response(token, "reject") is True
    with pytest.raises(ValueError, match="already resolved"):
        await response(
            token, "approve", **({"always_approve": True} if kind == "tool" else {})
        )
    assert (await task)["decision"] == "reject"
    assert not getattr(sess, "always_allow_tools", set())
    svc._persist_approval_decision.assert_called_once()
    assert svc._broadcast_session.call_count == 2  # requested + one resolved


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["tool", "mcp"])
async def test_cancelled_prompt_and_invalid_decision_cannot_grant_permission(kind):
    svc = service()
    task, token = await request(svc, session("A"), kind)
    response = getattr(svc, kind + "_approval_response")
    with pytest.raises(ValueError, match="must be"):
        await response(token, "bogus")
    assert not task.done()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert await response(token, "approve") is False
    assert not getattr(svc, "_pending_" + kind + "_approvals")


@pytest.mark.asyncio
async def test_late_old_response_cannot_resolve_new_request_with_reused_provider_id():
    svc = service()
    sess = session("A")
    first, old = await request(svc, sess, "tool")
    await svc.tool_approval_response(old, "approve")
    await first
    second, new = await request(svc, sess, "tool")
    assert new != old
    await svc.tool_approval_response(old, "approve")
    assert not second.done()
    await svc.tool_approval_response(new, "reject")
    await second


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["tool", "mcp"])
@pytest.mark.parametrize("commit_before_error", [False, True])
async def test_storage_failure_keeps_waiter_retryable_and_permissions_unchanged(
    kind, commit_before_error, monkeypatch, tmp_path
):
    from omniagents.core.conversation import recorder as recorder_module
    from omniagents.core.conversation.recorder import ConversationRecorder
    from omniagents.core.conversation.store import SqliteConversationStore
    from omniagents.core.conversation.model import derive_item_id

    store = SqliteConversationStore(path=str(tmp_path / "approval.db"))
    rec = ConversationRecorder(store)
    monkeypatch.setattr(recorder_module, "recorder_for_session", lambda *a, **kw: rec)
    svc = service()
    svc._persist_approval_decision = AgentService._persist_approval_decision.__get__(
        svc
    )
    sess = session("A")
    task, token = await request(svc, sess, kind)
    sess._pending_approval_tool_names = {token: "example"}
    response = getattr(svc, kind + "_approval_response")
    kwargs = {"always_approve": True} if kind == "tool" else {}
    original = rec.commit_approval_decision

    def broken(*args, **kw):
        assert not getattr(svc, f"_pending_{kind}_approvals")[token][0].done()
        assert not getattr(sess, "always_allow_tools", set())
        if commit_before_error:
            original(*args, **kw)
        raise OSError("storage unavailable")

    try:
        monkeypatch.setattr(rec, "commit_approval_decision", broken)
        with pytest.raises(OSError, match="storage unavailable"):
            await response(token, "approve", **kwargs)
        assert not task.done()
        assert not svc._resolved_approvals
        assert not getattr(sess, "always_allow_tools", set())
        assert svc._broadcast_session.call_count == 1
        monkeypatch.setattr(rec, "commit_approval_decision", original)
        if commit_before_error:
            with pytest.raises(ValueError, match="already resolved"):
                await response(token, "reject")
            assert not task.done()
        assert await response(token, "approve", **kwargs)
        assert (await task)["decision"] == "approve"
        item = store.get_item(
            sess.id, derive_item_id(sess.id, f"approval:{kind}:{token}")
        )
        assert item.content["approval_response"]["decision"] == "approve"
        assert await response(token, "approve", **kwargs)
        assert svc._broadcast_session.call_count == 2
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        store.close()
