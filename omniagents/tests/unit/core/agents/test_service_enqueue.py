"""Tests for AgentService.enqueue_message — the background-injection queue.

Covers:
- append-only items land in history without calling start_run
- trigger_run items call start_run and the prompt lands in history exactly once
  (start_run is the source of the append — drainer must not duplicate)
- two queued runs serialize, never overlapping
- a user-initiated run blocks the drainer until it finishes
- queue_full returns ok=false without raising
- queue_status reflects depth / draining / run_active
- drainer survives a failing item and processes the next one
"""

import asyncio
from dataclasses import dataclass, field
import types

import pytest

from omniagents.execution import AgentHostResources
from omniagents.core.agents import service as service_module
from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.session.manager import QueuedItem
from omniagents.rpc.generated.gui_v2.models import StartRunParams, EnqueueMessageParams


def test_protocol_accepts_direct_and_queued_attachment_parts():
    parts = [{"type": "input_image", "image_url": "data:image/png;base64,AA=="}]
    direct = StartRunParams.model_validate(
        {"prompt": "image", "environment_selection": {"mode": "none"}, "content": parts}
    )
    queued = EnqueueMessageParams.model_validate(
        {"session_id": "a", "content": "image", "input_content": parts}
    )
    assert direct.content == parts
    assert queued.input_content == parts
    assert (
        QueuedItem(content="image", input_content=parts).to_dict()["input_content"]
        == parts
    )
    assert (
        StartRunParams.model_validate(
            {
                "prompt": "text",
                "environment_selection": {"mode": "none"},
                "content": "plain text",
            }
        ).content
        == "plain text"
    )


@pytest.mark.asyncio
async def test_pending_acceptance_survives_session_reload_and_lost_reply(monkeypatch):
    service = make_service(monkeypatch)
    with monkeypatch.context() as patcher:
        patcher.setattr(service, "_ensure_drainer", lambda session: None)
        accepted = await service.enqueue_message(
            "restart-queue", "survive", submission_id="send"
        )
    service._session_manager.drop("restart-queue")
    snapshot = await service.list_queue("restart-queue")
    assert [item["content"] for item in snapshot["items"]] == ["survive"]
    session = service._session_manager.get("restart-queue")
    await asyncio.wait_for(session.drainer_task, timeout=2)
    assert (
        await service.enqueue_message("restart-queue", "survive", submission_id="send")
        == accepted
    )
    assert session.drainer_task.done()
    assert (await service.list_queue(session.id))["items"] == []


@pytest.mark.asyncio
async def test_foreign_claim_survives_reload_without_dispatch(monkeypatch):
    from omniagents.core.session import queue_inbox

    service = make_service(monkeypatch)
    with monkeypatch.context() as patcher:
        patcher.setattr(service, "_ensure_drainer", lambda session: None)
        accepted = await service.enqueue_message("foreign-queue", "do not replay")
    session = service._session_manager.get("foreign-queue")
    assert queue_inbox.claim(
        service._queue_store(session), session.id, accepted["id"], "dead-owner"
    )
    service._session_manager.drop(session.id)
    snapshot = await service.list_queue(session.id)
    # A live foreign claim is a run about to start: listed plainly as pending,
    # never dispatched a second time from here, and still cancellable.
    assert snapshot["items"][0]["state"] == "pending"
    assert "error" not in snapshot["items"][0] or snapshot["items"][0]["error"] is None
    assert not queue_inbox.claim(
        service._queue_store(session), session.id, accepted["id"], "second-dispatcher"
    )
    assert (await service.cancel_queued_message(session.id, accepted["id"]))["ok"]


@pytest.mark.asyncio
async def test_buffered_notification_survives_reload(monkeypatch):
    service = make_service(monkeypatch)
    monkeypatch.setattr(service, "_ensure_notification_flusher", lambda session: None)
    result = await service.enqueue_notification(
        "buffer-reload", "survive notification", source="test"
    )
    assert result["ok"]
    service._session_manager.drop("buffer-reload")
    await service.list_queue("buffer-reload")
    session = service._session_manager.get("buffer-reload")
    assert len(session.pending_notifications) == 1
    assert "survive notification" in str(session.pending_notifications)


@pytest.mark.asyncio
async def test_queue_refresh_does_not_unarchive_canonical_thread(monkeypatch):
    from omniagents.core.conversation.model import ThreadStatus

    service = make_service(monkeypatch)
    monkeypatch.setattr(service, "_ensure_drainer", lambda session: None)
    await service.enqueue_message("archive-queue", "pending")
    session = service._session_manager.get("archive-queue")
    store = service._queue_store(session)
    store.mutate_thread(
        session.id, lambda thread: setattr(thread, "status", ThreadStatus.ARCHIVED)
    )
    assert not session.archived
    assert (await service.enqueue_message(session.id, "rejected"))[
        "reason"
    ] == "session_archived"
    assert session.archived
    assert store.get_thread(session.id).status == ThreadStatus.ARCHIVED


@pytest.mark.asyncio
async def test_durable_rejection_is_visible_without_blocking_later_work(monkeypatch):
    from omniagents.core.session.submissions import submit_once

    service = make_service(monkeypatch)
    calls = []

    async def fake_start_run(prompt, session_id=None, submission_id=None, **kwargs):
        session = service._session_manager.get(session_id)

        async def invoke():
            calls.append(prompt)
            if prompt == "reject":
                raise ValueError("configuration invalid")
            return {"run_id": "ok", "session_id": session_id}

        return await submit_once(
            session,
            service._queue_store(session),
            submission_id,
            {"prompt": prompt},
            invoke,
        )

    service.start_run = fake_start_run
    await service.enqueue_message("reject-queue", "reject")
    await service.enqueue_message("reject-queue", "after")
    session = service._session_manager.get("reject-queue")
    await asyncio.wait_for(session.drainer_task, timeout=2)
    assert calls == ["reject", "after"]
    items = (await service.list_queue(session.id))["items"]
    assert len(items) == 1 and items[0]["state"] == "failed"
    assert "configuration invalid" in items[0]["error"]
    assert (await service.cancel_queued_message(session.id, items[0]["id"]))["ok"]


@pytest.mark.asyncio
async def test_snapshot_includes_one_history_queue_and_journal_boundary(monkeypatch):
    service = make_service(monkeypatch)
    session = service._session_manager.get_or_create("snapshot")
    session.append_message({"role": "user", "content": "existing"})
    from omniagents.core.conversation.recorder import ensure_conversation_thread

    ensure_conversation_thread(session).record_user_message(
        session.id, run_id=None, content="existing"
    )
    monkeypatch.setattr(service, "_ensure_drainer", lambda session: None)
    await service.enqueue_message(session.id, "next")
    session.pending_client_requests["q"] = {
        "request_id": "q",
        "session_id": session.id,
        "function": "escalate",
        "args": {"message": "question"},
    }
    result = await service.queue_status(session.id, include_snapshot=True)
    snapshot = result["snapshot"]
    assert snapshot["items"]
    assert snapshot["queue"][0]["content"] == "next"
    assert snapshot["pending_requests"][0]["request_id"] == "q"
    assert snapshot["stream_id"] == session.event_log.stream_id
    assert snapshot["last_seq"] == session.event_log.last_seq
    session.pending_client_requests["q"]["args"]["message"] = "changed"
    assert snapshot["pending_requests"][0]["args"]["message"] == "question"


@dataclass
class DummyAgent:
    name: str = "agent"
    tools: list = field(default_factory=list)
    instructions: str = ""
    model: str = "model"
    model_settings: dict | None = None
    mcp_servers: list = field(default_factory=list)

    def set_approval_callback(self, callback):
        self.approval_callback = callback


async def default_build_agent(settings, primary_server, stub_app):
    return DummyAgent()


def make_spec(**overrides):
    base = {
        "name": "Agent",
        "welcome_text": "Welcome",
        "get_agent_instructions": overrides.pop("get_agent_instructions", lambda: ""),
        "build_agent": overrides.pop("build_agent", default_build_agent),
    }
    base.update(overrides)
    return AgentSpec(**base)


def attach_capture(service):
    events = []

    async def capture(
        self, session, method, payload, *, queue_if_unseen=False, track_live=False
    ):
        events.append({"method": method, "payload": payload})
        return True

    service._broadcast_session = types.MethodType(capture, service)
    return events


def make_service(monkeypatch, stream_result=None, stream_side_effect=None):
    """Build a service with a stubbed stream_agent that completes quickly."""

    async def stream(*args, **kwargs):
        if stream_side_effect is not None:
            raise stream_side_effect
        return stream_result or {
            "usage": {"input_tokens": 1},
            "model": "stub",
            "max_input_tokens": 1024,
            "max_output_tokens": 256,
        }

    monkeypatch.setattr(service_module, "stream_agent", stream)
    spec = make_spec()
    service = AgentService(
        spec,
        AgentHostResources.create("test-unit-core-agents-service_enqueue-host"),
    )
    service.channel = object()
    attach_capture(service)
    return service


# ---------------------------------------------------------------------------
# Append-only path
# Attachments must reach the run without becoming queue display text.


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["user", "assistant", "system"])
async def test_queued_messages_dispatch_once_with_their_role(monkeypatch, role):
    service = make_service(monkeypatch)
    calls = []

    async def start(**kwargs):
        calls.append(kwargs)
        return {"run_id": str(len(calls))}

    service.start_run = start
    for _ in range(2):
        await service.enqueue_message("canonical-queue", "same text", role=role)
        await service._session_manager.get("canonical-queue").drainer_task
    assert [call["prompt_role"] for call in calls] == [role, role]
    assert [call["prompt"] for call in calls] == ["same text", "same text"]
    assert calls[0]["submission_id"] != calls[1]["submission_id"]
    assert not (await service.list_queue("canonical-queue"))["items"]


@pytest.mark.asyncio
async def test_queued_attachments_reach_run(monkeypatch):
    service = make_service(monkeypatch)
    captured = []

    async def start(**kwargs):
        captured.append(kwargs)
        return {"run_id": "queued-attachment"}

    service.start_run = start
    content = [{"type": "input_image", "image_url": "data:image/png;base64,AA=="}]
    result = await service.enqueue_message(
        "attachments", "Describe this", input_content=content
    )
    assert result["ok"]
    session = service._session_manager.get_or_create("attachments")
    await asyncio.wait_for(session.drainer_task, timeout=1)
    assert captured[0]["content"] == content
    assert captured[0]["prompt"] == "Describe this"


# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_always_starts_a_turn(monkeypatch):
    service = make_service(monkeypatch)
    sess_id = "sess-append"

    started = []

    async def fake_start_run(*args, **kwargs):
        started.append(kwargs)
        return {"run_id": "x", "session_id": sess_id}

    service.start_run = fake_start_run  # type: ignore[assignment]

    res = await service.enqueue_message(sess_id, "ping", role="system")
    assert res["ok"] is True
    assert res["depth"] >= 1

    sess = service._session_manager.get_or_create(sess_id)
    await asyncio.wait_for(sess.drainer_task, timeout=1)

    assert len(started) == 1
    assert started[0]["prompt"] == "ping"
    assert started[0]["prompt_role"] == "system"
    assert not sess.message_queue


# ---------------------------------------------------------------------------
# trigger_run path — start_run owns the append
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_trigger_run_calls_start_run_with_prompt(monkeypatch):
    service = make_service(monkeypatch)
    sess_id = "sess-run"

    captured = []

    async def fake_start_run(
        prompt,
        session_id=None,
        variables=None,
        content=None,
        safe_tool_overrides=None,
        **_,
    ):
        captured.append(
            {
                "prompt": prompt,
                "session_id": session_id,
                "variables": variables,
                "safe_tool_overrides": safe_tool_overrides,
            }
        )
        sess = service._session_manager.get_or_create(session_id)
        # Real start_run appends the user turn — mimic that so the
        # drainer doesn't double-append.
        sess.append_message({"role": "user", "content": prompt, "type": "message"})
        # No current_task set: drainer's await guard is None-safe.
        return {"run_id": "r1", "session_id": session_id}

    service.start_run = fake_start_run  # type: ignore[assignment]

    res = await service.enqueue_message(
        sess_id,
        "go",
        variables={"k": "v"},
        safe_tool_overrides={"safe_tool_names": ["read"]},
    )
    assert res["ok"] is True

    sess = service._session_manager.get_or_create(sess_id)
    await asyncio.wait_for(sess.drainer_task, timeout=1)

    assert len(captured) == 1
    assert captured[0]["prompt"] == "go"
    assert captured[0]["session_id"] == sess_id
    assert captured[0]["variables"] == {"k": "v"}
    assert captured[0]["safe_tool_overrides"] == {"safe_tool_names": ["read"]}

    # Exactly one user message in history — drainer must not pre-append
    # when start_run will do it.
    user_msgs = [m for m in sess.history if m.get("role") == "user"]
    assert len(user_msgs) == 1
    assert user_msgs[0]["content"] == "go"


@pytest.mark.asyncio
async def test_flush_notifications_preserves_safe_tool_overrides(monkeypatch):
    service = make_service(monkeypatch)
    sess_id = "sess-notify-safe"
    sess = service._session_manager.get_or_create(sess_id)
    captured = []

    async def fake_start_run(
        prompt,
        session_id=None,
        variables=None,
        safe_tool_overrides=None,
        prompt_role="user",
        **_,
    ):
        captured.append(
            {
                "prompt": prompt,
                "session_id": session_id,
                "variables": variables,
                "safe_tool_overrides": safe_tool_overrides,
                "prompt_role": prompt_role,
            }
        )
        return {"run_id": "r1", "session_id": session_id}

    service.start_run = fake_start_run  # type: ignore[assignment]
    await service.enqueue_notification(
        sess.id, "tick", "goal.tick", safe_tool_overrides={"safe_tool_patterns": [".*"]}
    )

    await service._flush_notifications(sess)
    await asyncio.wait_for(sess.drainer_task, timeout=1)

    assert captured[0]["prompt"] == "tick"
    assert captured[0]["prompt_role"] == "assistant"
    assert captured[0]["safe_tool_overrides"] == {"safe_tool_patterns": [".*"]}


# ---------------------------------------------------------------------------
# Serialization — two queued runs never overlap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_queued_runs_serialize(monkeypatch):
    service = make_service(monkeypatch)
    sess_id = "sess-serial"

    in_flight = {"count": 0, "max": 0}
    order = []

    async def fake_start_run(prompt, session_id=None, **_):
        sess = service._session_manager.get_or_create(session_id)
        order.append(("start", prompt))
        in_flight["count"] += 1
        in_flight["max"] = max(in_flight["max"], in_flight["count"])

        async def _worker():
            try:
                await asyncio.sleep(0.05)
            finally:
                in_flight["count"] -= 1
                order.append(("end", prompt))
                if sess.current_task is asyncio.current_task():
                    sess.current_task = None

        sess.current_task = asyncio.create_task(_worker())
        return {"run_id": prompt, "session_id": session_id}

    service.start_run = fake_start_run  # type: ignore[assignment]

    await service.enqueue_message(sess_id, "first")
    await service.enqueue_message(sess_id, "second")

    sess = service._session_manager.get_or_create(sess_id)
    await asyncio.wait_for(sess.drainer_task, timeout=2)

    # The two runs must be strictly ordered: start→end of first
    # completes before second starts.
    assert order == [
        ("start", "first"),
        ("end", "first"),
        ("start", "second"),
        ("end", "second"),
    ]
    assert in_flight["max"] == 1


# ---------------------------------------------------------------------------
# A user-initiated run blocks the drainer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drainer_waits_for_active_user_run(monkeypatch):
    service = make_service(monkeypatch)
    sess_id = "sess-active"
    sess = service._session_manager.get_or_create(sess_id)

    # Simulate a user-initiated run already in flight.
    user_run_done = asyncio.Event()

    async def user_run():
        await user_run_done.wait()

    sess.current_task = asyncio.create_task(user_run())

    appended_at = []

    async def fake_start_run(*args, **kwargs):
        appended_at.append("never")
        return {"run_id": "x", "session_id": sess_id}

    service.start_run = fake_start_run  # type: ignore[assignment]

    await service.enqueue_message(sess_id, "after-active", role="system")

    # Drainer is alive but parked on the user run's current_task.
    await asyncio.sleep(0.05)
    assert sess.drainer_task is not None
    assert not sess.drainer_task.done()
    assert sess.history == [] or sess.history[-1].get("content") != "after-active"

    # Release the user run; drainer should now complete the append.
    user_run_done.set()
    sess.current_task = None
    await asyncio.wait_for(sess.drainer_task, timeout=1)

    assert appended_at == ["never"]


# ---------------------------------------------------------------------------
# queue_full
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_full_returns_error(monkeypatch):
    service = make_service(monkeypatch)
    sess_id = "sess-full"
    sess = service._session_manager.get_or_create(sess_id)

    # Park the drainer so items pile up without being consumed.
    sess.current_task = asyncio.create_task(asyncio.sleep(5))

    # Pre-fill the queue to capacity.
    for i in range(sess.message_queue_max):
        await service.enqueue_message(sess_id, f"msg-{i}")

    res = await service.enqueue_message(sess_id, "overflow")
    assert res["ok"] is False
    assert res["reason"] == "queue_full"

    sess.current_task.cancel()


# ---------------------------------------------------------------------------
# queue_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_status_reflects_state(monkeypatch):
    service = make_service(monkeypatch)
    sess_id = "sess-status"
    sess = service._session_manager.get_or_create(sess_id)

    status = await service.queue_status(sess_id)
    assert status == {
        "session_id": sess_id,
        "depth": 0,
        "draining": False,
        "run_active": False,
        "active_run_id": None,
    }

    # Park a fake user run and enqueue an item — drainer parks on current_task.
    sess.current_task = asyncio.create_task(asyncio.sleep(5))
    await service.enqueue_message(sess_id, "stuck")
    # Yield so the drainer task is actually scheduled before we observe it.
    await asyncio.sleep(0)

    status = await service.queue_status(sess_id)
    assert status["draining"] is True
    assert status["run_active"] is True

    sess.current_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sess.current_task
    sess.current_task = None
    await asyncio.wait_for(sess.drainer_task, timeout=1)

    status = await service.queue_status(sess_id)
    assert status["draining"] is False
    assert status["depth"] == 0


# ---------------------------------------------------------------------------
# Drainer survives a failing item
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drainer_marks_a_failed_dispatch_and_continues_with_later_work(
    monkeypatch,
):
    service = make_service(monkeypatch)
    sess_id = "sess-resilient"

    calls = []

    async def fake_start_run(prompt, session_id=None, **_):
        calls.append(prompt)
        if prompt == "boom":
            raise RuntimeError("nope")
        return {"run_id": prompt, "session_id": session_id}

    service.start_run = fake_start_run  # type: ignore[assignment]

    await service.enqueue_message(sess_id, "boom")
    await service.enqueue_message(sess_id, "ok")

    sess = service._session_manager.get_or_create(sess_id)
    await asyncio.wait_for(sess.drainer_task, timeout=1)

    # The failed item stays visible with its reason; the next one still runs.
    assert calls == ["boom", "ok"]
    snapshot = await service.list_queue(sess_id)
    assert [item["state"] for item in snapshot["items"]] == ["failed"]
    assert "nope" in snapshot["items"][0]["error"]
    assert len(sess.message_queue) == 1


# ---------------------------------------------------------------------------
# Visual-management slice: list_queue, cancel_queued_message, queue_changed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_returns_item_id(monkeypatch):
    service = make_service(monkeypatch)
    sess_id = "sess-id"

    # Park drainer so items stay queued.
    sess = service._session_manager.get_or_create(sess_id)
    sess.current_task = asyncio.create_task(asyncio.sleep(5))

    res = await service.enqueue_message(sess_id, "first")
    assert "id" in res and isinstance(res["id"], str) and res["id"]
    assert res["depth"] == 1
    assert res["ok"] is True

    sess.current_task.cancel()


@pytest.mark.asyncio
async def test_list_queue_returns_items_in_order(monkeypatch):
    service = make_service(monkeypatch)
    sess_id = "sess-list"

    sess = service._session_manager.get_or_create(sess_id)
    sess.current_task = asyncio.create_task(asyncio.sleep(5))

    r1 = await service.enqueue_message(sess_id, "one", role="user", source="cron")
    r2 = await service.enqueue_message(sess_id, "two")
    r3 = await service.enqueue_message(sess_id, "three")

    snapshot = await service.list_queue(sess_id)
    assert snapshot["session_id"] == sess_id
    assert snapshot["depth"] == 3
    ids = [it["id"] for it in snapshot["items"]]
    assert ids == [r1["id"], r2["id"], r3["id"]]

    item_two = snapshot["items"][1]
    assert item_two["content"] == "two"
    assert "trigger_run" not in item_two
    assert item_two["role"] == "user"
    # Optional fields round-trip
    assert snapshot["items"][0]["source"] == "cron"

    sess.current_task.cancel()


@pytest.mark.asyncio
async def test_cancel_queued_message_removes_item(monkeypatch):
    service = make_service(monkeypatch)
    sess_id = "sess-cancel"

    sess = service._session_manager.get_or_create(sess_id)
    sess.current_task = asyncio.create_task(asyncio.sleep(5))

    r1 = await service.enqueue_message(sess_id, "keep")
    r2 = await service.enqueue_message(sess_id, "drop")
    r3 = await service.enqueue_message(sess_id, "keep-tail")

    res = await service.cancel_queued_message(sess_id, r2["id"])
    assert res == {"ok": True, "id": r2["id"], "depth": 2}

    snapshot = await service.list_queue(sess_id)
    assert [it["id"] for it in snapshot["items"]] == [r1["id"], r3["id"]]

    sess.current_task.cancel()


@pytest.mark.asyncio
async def test_cancel_unknown_id_returns_not_found(monkeypatch):
    service = make_service(monkeypatch)
    sess_id = "sess-cancel-miss"

    sess = service._session_manager.get_or_create(sess_id)
    sess.current_task = asyncio.create_task(asyncio.sleep(5))
    await service.enqueue_message(sess_id, "real")

    res = await service.cancel_queued_message(sess_id, "nonexistent")
    assert res == {"ok": False, "reason": "not_found", "id": "nonexistent"}

    sess.current_task.cancel()


@pytest.mark.asyncio
async def test_cancel_after_drain_returns_not_found(monkeypatch):
    """Once the drainer pops an item, cancel can't find it.

    The contract is: cancelling an actively-running turn requires stop_run,
    not cancel_queued_message.
    """
    service = make_service(monkeypatch)
    sess_id = "sess-drained"

    # No current_task → drainer pops immediately on enqueue.
    fired = []

    async def fake_start_run(prompt, session_id=None, **_):
        fired.append(prompt)
        return {"run_id": "r", "session_id": session_id}

    service.start_run = fake_start_run  # type: ignore[assignment]

    res = await service.enqueue_message(sess_id, "go")
    sess = service._session_manager.get_or_create(sess_id)
    await asyncio.wait_for(sess.drainer_task, timeout=1)

    # Item has been processed; cancel should miss.
    cancel = await service.cancel_queued_message(sess_id, res["id"])
    assert cancel["ok"] is False
    assert cancel["reason"] == "not_found"
    assert fired == ["go"]


@pytest.mark.asyncio
async def test_queued_item_stays_visible_during_in_flight_run(monkeypatch):
    """Regression: drainer must NOT pop items before waiting on current_task.

    If pop happens immediately on drainer spawn, the item flashes through
    the queue (broadcast depth=1 from enqueue → broadcast depth=0 from
    drainer pop) before the in-flight run even starts blocking, so React
    batches the renders and the "Up next" panel never visibly shows the
    queued message.

    The correct ordering is: wait_for_current_task → pop → broadcast.
    This test pins that ordering by asserting the item is still in
    sess.message_queue while a user run is active, and only gets removed
    once that run finishes.
    """
    service = make_service(monkeypatch)
    sess_id = "sess-visible"

    started = []

    async def fake_start_run(prompt, session_id=None, **_):
        started.append(prompt)
        return {"run_id": "r", "session_id": session_id}

    service.start_run = fake_start_run  # type: ignore[assignment]

    sess = service._session_manager.get_or_create(sess_id)

    # Simulate a long-running user-initiated task.
    user_run_done = asyncio.Event()

    async def user_run():
        await user_run_done.wait()

    sess.current_task = asyncio.create_task(user_run())

    # Enqueue while the user run is in flight.
    res = await service.enqueue_message(sess_id, "queued-msg")
    assert res["ok"] is True

    # Yield to let the drainer task get scheduled and run up to its first
    # await (which should block on current_task — NOT pop the item yet).
    await asyncio.sleep(0.01)

    # The drainer is alive but parked. The item must still be in the queue
    # so list_queue / queue_changed reflect it to clients.
    assert sess.drainer_task is not None and not sess.drainer_task.done()
    assert len(sess.message_queue) == 1
    assert sess.message_queue[0].content == "queued-msg"
    assert started == []  # start_run hasn't fired yet

    # list_queue must also see it (this is the snapshot a reconnecting
    # client would receive — same data the broadcast carries).
    snap = await service.list_queue(sess_id)
    assert snap["depth"] == 1
    assert snap["items"][0]["content"] == "queued-msg"

    # Release the user run; drainer wakes up, pops, fires start_run.
    user_run_done.set()
    sess.current_task = None
    await asyncio.wait_for(sess.drainer_task, timeout=1)

    assert len(sess.message_queue) == 0
    assert started == ["queued-msg"]


@pytest.mark.asyncio
async def test_queue_changed_broadcasts_on_enqueue_pop_and_cancel(monkeypatch):
    service = make_service(monkeypatch)
    # Re-attach capture so we can inspect broadcasts for this test.
    events = attach_capture(service)

    sess_id = "sess-broadcast"
    sess = service._session_manager.get_or_create(sess_id)
    sess.current_task = asyncio.create_task(asyncio.sleep(5))

    # Two enqueues → two queue_changed broadcasts with growing depth.
    r1 = await service.enqueue_message(sess_id, "alpha")
    r2 = await service.enqueue_message(sess_id, "beta")

    qc_events = [e for e in events if e["method"] == "queue_changed"]
    assert len(qc_events) == 2
    assert qc_events[0]["payload"]["depth"] == 1
    assert qc_events[1]["payload"]["depth"] == 2
    assert [it["id"] for it in qc_events[1]["payload"]["items"]] == [r1["id"], r2["id"]]

    # Cancel → another queue_changed with the cancelled item gone.
    await service.cancel_queued_message(sess_id, r1["id"])
    qc_events = [e for e in events if e["method"] == "queue_changed"]
    assert len(qc_events) == 3
    assert qc_events[-1]["payload"]["depth"] == 1
    assert qc_events[-1]["payload"]["items"][0]["id"] == r2["id"]

    # Release the user-run; drainer pops r2 and fires another broadcast
    # *before* running it.
    sess.current_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sess.current_task
    sess.current_task = None
    await asyncio.wait_for(sess.drainer_task, timeout=1)

    qc_events = [e for e in events if e["method"] == "queue_changed"]
    # At least one more broadcast (pop). May be more if the drainer
    # broadcasts at additional lifecycle points later; assert the empty
    # final state rather than exact count.
    assert qc_events[-1]["payload"]["depth"] == 0
    assert qc_events[-1]["payload"]["items"] == []


@pytest.mark.asyncio
async def test_active_run_and_helpers_keep_provider_while_next_queued_run_switches(
    monkeypatch, tmp_path
):
    from omniagents.core import models
    from omniagents.core.models.runtime import resolve_service_model_runtime

    store = models.ModelConfigStore(config_dir=lambda: tmp_path, single_provider=True)
    store.save_config(
        {
            "default": "first/a",
            "provider_scope_version": 1,
            "providers": {
                name: {
                    "type": "openai-compatible",
                    "base_url": f"https://{name}.invalid/v1",
                    "api_key": "test-only",
                    "models": {"a": {"model": "a"}},
                }
                for name in ("first", "second")
            },
        }
    )
    monkeypatch.setattr(models, "_ACTIVE_STORE", store)
    service = make_service(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    observed = []

    async def stream(agent, prompt, session, *args, **kwargs):
        observed.append(resolve_service_model_runtime(service, session)[0])
        if prompt == "initial":
            entered.set()
            await release.wait()
            observed.append(resolve_service_model_runtime(service, session)[0])
        return {"usage": {}, "model": "stub"}

    monkeypatch.setattr(service_module, "stream_agent", stream)
    await service.start_run(
        "initial", environment_selection={"mode": "none"}, session_id="pin-provider"
    )
    try:
        await asyncio.wait_for(entered.wait(), 3)
        config = store.load_config()
        config["default"] = "second/a"
        store.save_config(config)
        await service.enqueue_message("pin-provider", "followup")
        release.set()
        await asyncio.wait_for(service.get_session("pin-provider").drainer_task, 5)
        assert observed == ["first/a", "first/a", "second/a"]
    finally:
        release.set()
