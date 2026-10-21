"""Ownership must exist before delivery and end on every terminal path."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from omniagents.core.agents.service import AgentService


def setup():
    svc = object.__new__(AgentService)
    svc.channel = None
    svc._pending_client_requests = {}
    svc._client_request_sessions = {}
    svc._pending_mcp_approvals = {}
    svc._broadcast_session = AsyncMock()
    sess = SimpleNamespace(
        id="A",
        user_id="owner-A",
        channels=[object()],
        pending_client_requests={},
        pending_elicitations={},
    )
    return svc, sess


def assert_empty(svc, sess):
    assert not svc._pending_client_requests
    assert not svc._client_request_sessions
    assert not sess.pending_client_requests


@pytest.mark.asyncio
async def test_immediate_reply_is_consumed_and_identical_retry_is_authorized():
    svc, sess = setup()
    ids = []

    async def notify(channel, method, payload):
        rid = payload["request_id"]
        ids.append(rid)
        assert (
            svc._resolve_rpc_resource_owner("client_response", {"request_id": rid})[0]
            == "owner-A"
        )
        assert rid in sess.pending_client_requests
        assert await svc.client_response(rid, True, {"reply": "answer"}) is True
        return True

    svc._safe_notify = notify
    result = await svc.request_client_function(
        "escalate", session=sess, expect_result=True, timeout_ms=100
    )
    assert result == {"ok": True, "result": {"reply": "answer"}, "error": None}
    assert_empty(svc, sess)
    assert (
        svc._resolve_rpc_resource_owner("client_response", {"request_id": ids[0]})[0]
        == "owner-A"
    )
    assert await svc.client_response(ids[0], True, {"reply": "answer"}) is True
    with pytest.raises(ValueError, match="different response"):
        await svc.client_response(ids[0], True, {"reply": "different"})
    assert svc._broadcast_session.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("during_delivery", [False, True])
async def test_cancellation_cleans_waiter_and_replay_and_rejects_late_reply(
    during_delivery,
):
    svc, sess = setup()
    sent = asyncio.Event()

    async def notify(*args):
        sent.set()
        if during_delivery:
            await asyncio.Future()
        return True

    svc._safe_notify = notify
    task = asyncio.create_task(
        svc.request_client_function("escalate", session=sess, expect_result=True)
    )
    await sent.wait()
    rid = next(iter(sess.pending_client_requests))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_empty(svc, sess)
    assert await svc.client_response(rid, True, {"reply": "late"}) is False
    assert svc._broadcast_session.call_args.args[2]["reason"] == "cancelled"


@pytest.mark.asyncio
async def test_timeout_cleans_every_index_and_publishes_resolution():
    svc, sess = setup()
    svc._safe_notify = AsyncMock(return_value=False)
    assert (
        await svc.request_client_function(
            "escalate", session=sess, expect_result=True, timeout_ms=1
        )
        is None
    )
    assert_empty(svc, sess)
    assert svc._broadcast_session.call_args.args[2]["reason"] == "timeout"


@pytest.mark.asyncio
async def test_run_cleanup_cancels_waiter_without_leaking_global_entry():
    svc, sess = setup()
    svc._safe_notify = AsyncMock(return_value=True)
    task = asyncio.create_task(
        svc.request_client_function("escalate", session=sess, expect_result=True)
    )
    await asyncio.sleep(0)
    rid = next(iter(sess.pending_client_requests))
    await svc._cancel_client_request(rid, "run_ended")
    svc._clear_live_state(sess)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_empty(svc, sess)
    assert await svc.client_response(rid, True, {}) is False
    assert svc._broadcast_session.await_count == 1


@pytest.mark.asyncio
async def test_unknown_response_is_not_acknowledged():
    svc, _ = setup()
    assert await svc.client_response("unknown", True, {}) is False


@pytest.mark.asyncio
async def test_fallback_live_state_cleanup_removes_global_waiter():
    svc, sess = setup()
    svc._safe_notify = AsyncMock(return_value=True)
    task = asyncio.create_task(
        svc.request_client_function("escalate", session=sess, expect_result=True)
    )
    await asyncio.sleep(0)
    svc._clear_live_state(sess)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_empty(svc, sess)


@pytest.mark.asyncio
async def test_cancelling_a_does_not_resolve_b():
    svc, a = setup()
    _, b = setup()
    b.id, b.user_id = "B", "owner-B"
    svc._safe_notify = AsyncMock(return_value=True)
    ta = asyncio.create_task(
        svc.request_client_function("escalate", session=a, expect_result=True)
    )
    tb = asyncio.create_task(
        svc.request_client_function("escalate", session=b, expect_result=True)
    )
    await asyncio.sleep(0)
    aid, bid = next(iter(a.pending_client_requests)), next(
        iter(b.pending_client_requests)
    )
    assert aid != bid
    ta.cancel()
    with pytest.raises(asyncio.CancelledError):
        await ta
    assert not tb.done()
    assert await svc.client_response(aid, True, {}) is False
    assert await svc.client_response(bid, True, {"reply": "B"}) is True
    assert (await tb)["result"] == {"reply": "B"}
    assert_empty(svc, b)


@pytest.mark.asyncio
async def test_receipts_are_bounded_and_do_not_retain_session_or_reply_content():
    svc, sess = setup()
    for index in range(2050):
        svc._remember_client_response(str(index), "fingerprint", sess)
    assert len(svc._resolved_client_requests) == 2048
    assert "0" not in svc._resolved_client_requests
    assert svc._resolved_client_requests["2049"][1] is not sess
    assert await svc.client_response("0", True, {}) is False
