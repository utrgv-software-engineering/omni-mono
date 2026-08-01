"""Plans and run diffs over the real ``/ws`` endpoint.

Drives the production write paths — the agent's own plan tools and real
``omniagents.workspace`` writes inside a run — then reads the result back
through the protocol: ``get_plan`` / ``get_run_diff``, the ``item_updated``
push, reconnect replay, and cross-user refusal.
"""

import asyncio
import json
import warnings
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agents import Agent
from omniagents.backends.server import app as server_app
from omniagents.builtin.tools import task_list_tools as tlt
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.conversation.store import reset_conversation_store
from omniagents.core.runtime import bridge as runner_bridge
from omniagents.rpc.auth import RPC_ERROR_AUTHORIZATION_DENIED
from omniagents import workspace as ws

ALICE = {"Authorization": "Bearer alice-token"}
BOB = {"Authorization": "Bearer bob-token"}

EXPERIMENTAL = ("get_plan", "get_run_diff", "item_updated")


async def mock_build_agent(settings, mcp, app):
    return Agent(
        name="plan_agent", tools=[], instructions="Plan", model="gpt-3.5-turbo"
    )


class _Ctx:
    def __init__(self, session_id):
        self.context = {"session_id": session_id}


def make_stream_agent(workspace_root):
    """A run that plans and edits files, using the production machinery.

    The plan tools and ``omniagents.workspace`` calls are the real ones; the
    only thing stubbed is the model turn that would have chosen them.
    """

    async def mock_stream_agent(agent, prompt, sess, channel, max_turns=20, **kwargs):
        from omniagents.core.runtime.bridge import _broadcast
        from omniagents.core.session import append_message

        service = kwargs.get("service")
        user_msg = {"role": "user", "content": prompt}
        sess.history.append(user_msg)
        append_message(sess.id, user_msg)

        # Emit one real notification first: ``_broadcast`` is what registers
        # this connection as a channel on the session, and without it every
        # later notification (including run_end) is queued instead of
        # delivered.
        await _broadcast(
            sess,
            "message_output",
            {
                "run_id": asyncio.current_task().get_name(),
                "session_id": sess.id,
                "content": f"working on: {prompt}",
            },
        )
        ws_token = ws.set_workspace(ws.HostWorkspace(root=str(workspace_root)))
        # The SAME context manager ``stream_agent`` uses. Hand-rolling the
        # bind/finalize/unbind sequence here is what let this harness drift
        # from production when the helper changed shape — and it only
        # surfaced because the change happened to raise.
        try:
            async with runner_bridge.run_diff_observer_scope(service, sess):
                ctx = _Ctx(sess.id)
                if prompt.startswith("PLAN"):
                    await tlt.task_create._original_func(
                        ctx, subject="write the module", description="d"
                    )
                    await tlt.task_create._original_func(
                        ctx, subject="add tests", description="d"
                    )
                    await tlt.task_update._original_func(
                        ctx, task_id="1", status="completed"
                    )
                    await tlt.task_update._original_func(
                        ctx, task_id="2", status="in_progress"
                    )
                if prompt.startswith("EDIT"):
                    await ws.write_text("mod.py", "v1\n")
                    await ws.write_text("mod.py", "v2\n")
                    await ws.write_text("mod.py", "v3\n")
                    await ws.write_text("scratch.txt", "temp\n")
                    await ws.remove("scratch.txt")
                if prompt.startswith("SHELL"):
                    # A shell command rewriting a file the API wrote: the
                    # scope's finalize() has to reconcile it.
                    await ws.write_text("mod.py", "def f( ):\n    pass\n")
                    (workspace_root / "mod.py").write_text("def f():\n    pass\n")
                # Let the fire-and-forget item_updated pushes drain before
                # the run ends, so the assertions see a settled stream.
                await asyncio.sleep(0)
                await asyncio.sleep(0)
        finally:
            ws.reset_workspace(ws_token)

    return mock_stream_agent


#: Every socket read in this harness is bounded. Without a deadline a
#: server that silently drops the channel — which is exactly what a failed
#: strict-validation push does — presents as a HANG to the outer suite
#: timeout instead of a failure, so the bug that killed the channel is
#: reported as "the suite hung" minutes later, in another file.
RECEIVE_TIMEOUT_SECONDS = 20.0


class WebSocketReadTimeout(AssertionError):
    """No frame arrived within :data:`RECEIVE_TIMEOUT_SECONDS`."""


class RpcWs:
    def __init__(self, ws_conn):
        self.ws = ws_conn
        self.next_id = 0
        self.notifications = []

    def receive_json(self, timeout: float = RECEIVE_TIMEOUT_SECONDS):
        """``ws.receive_json()`` with a deadline.

        Starlette's ``WebSocketTestSession.receive`` blocks forever on its
        portal. Reaching for the same portal with ``anyio.fail_after``
        bounds it; if a future Starlette drops either attribute we fall back
        to the unbounded call rather than failing the suite on plumbing.
        """
        portal = getattr(self.ws, "portal", None)
        stream = getattr(self.ws, "_send_rx", None)
        if portal is None or stream is None:  # pragma: no cover - version guard
            # A mitigation that can silently stop working has to say so: a
            # Starlette rename would drop the deadline and restore the
            # hang-until-the-outer-timeout behaviour with no signal at all.
            warnings.warn(
                "WebSocket read deadline unavailable — starlette's "
                "WebSocketTestSession no longer exposes portal/_send_rx, so "
                "a dropped channel will hang instead of failing. Re-point "
                "RpcWs.receive_json at the current internals.",
                RuntimeWarning,
                stacklevel=2,
            )
            return self.ws.receive_json()

        import anyio

        async def _receive():
            with anyio.fail_after(timeout):
                return await stream.receive()

        try:
            message = portal.call(_receive)
        except TimeoutError as exc:
            raise WebSocketReadTimeout(
                f"no WebSocket frame within {timeout}s — the server most "
                "likely dropped this channel (a notification that fails "
                "strict validation does exactly that)"
            ) from exc
        if message["type"] == "websocket.close":  # pragma: no cover - defensive
            raise WebSocketReadTimeout(
                f"channel closed: {message.get('code')} {message.get('reason')}"
            )
        return json.loads(message["text"])

    def call(self, method, **params):
        self.next_id += 1
        call_id = f"c{self.next_id}"
        self.ws.send_json(
            {"jsonrpc": "2.0", "id": call_id, "method": method, "params": params}
        )
        while True:
            message = self.receive_json()
            if message.get("id") == call_id and (
                "result" in message or "error" in message
            ):
                return message
            self.notifications.append(message)

    def result(self, method, **params):
        message = self.call(method, **params)
        assert "error" not in message, message
        return message["result"]

    def error(self, method, **params):
        message = self.call(method, **params)
        assert "error" in message, f"{method} unexpectedly succeeded: {message}"
        return message["error"]

    def await_run_end(self, run_id, limit=400):
        # A short run can finish before the ``start_run`` response is read,
        # in which case ``call`` already buffered its run_end; waiting on the
        # socket for a second one would block forever.
        for buffered in self.notifications:
            if (
                buffered.get("method") == "run_end"
                and buffered.get("params", {}).get("run_id") == run_id
            ):
                return buffered["params"]
        for _ in range(limit):
            message = self.receive_json()
            self.notifications.append(message)
            if (
                message.get("method") == "run_end"
                and message.get("params", {}).get("run_id") == run_id
            ):
                return message["params"]
        raise AssertionError(f"run_end for {run_id} never arrived")

    def pushed(self, method):
        return [n["params"] for n in self.notifications if n.get("method") == method]

    def handshake(self, experimental=EXPERIMENTAL):
        self.result(
            "initialize",
            protocol_version="1.0.0",
            identity={"name": "plan-tests", "version": "0.0.0"},
            platform={"os": "linux", "arch": "x86_64"},
            capabilities={
                "realtime": False,
                "mcp_apps": False,
                "client_functions": False,
                "approvals": True,
                "artifacts": False,
                "replay": True,
                "terminal": False,
                "experimental_operations": list(experimental),
                "disabled_notifications": [],
            },
        )
        self.ws.send_json({"jsonrpc": "2.0", "method": "initialized", "params": {}})


def _spec(name, security=None):
    return AgentSpec(
        name=name,
        welcome_text="Plan",
        get_agent_instructions=lambda: "Plan",
        build_agent=mock_build_agent,
        security_config=security,
    )


@pytest.fixture
def context(monkeypatch, tmp_path):
    from omniagents.core.agents import service as service_mod

    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    stream = make_stream_agent(workspace_root)
    monkeypatch.setattr(runner_bridge, "stream_agent", stream)
    monkeypatch.setattr(service_mod, "stream_agent", stream)
    monkeypatch.delenv("OMNIAGENTS_LEGACY_QUERY_AUTH", raising=False)
    tlt.reset_sessionless_plans()

    spec = _spec(
        "PlanAgent",
        {
            "providers": {
                "auth": {
                    "type": "token",
                    "tokens": {"alice-token": "alice", "bob-token": "bob"},
                }
            }
        },
    )
    app = server_app.build_app(spec=spec, default_workspace_root=str(workspace_root))
    client = TestClient(app)
    yield SimpleNamespace(
        client=client, service=app.state.agent_service, workspace=workspace_root
    )


@pytest.fixture
def single_user_context(monkeypatch, tmp_path):
    from omniagents.core.agents import service as service_mod

    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    stream = make_stream_agent(workspace_root)
    monkeypatch.setattr(runner_bridge, "stream_agent", stream)
    monkeypatch.setattr(service_mod, "stream_agent", stream)
    tlt.reset_sessionless_plans()

    app = server_app.build_app(
        spec=_spec("PlanAgentLocal"), default_workspace_root=str(workspace_root)
    )
    client = TestClient(app)
    yield SimpleNamespace(
        client=client, service=app.state.agent_service, workspace=workspace_root
    )


def connect(context, headers, experimental=EXPERIMENTAL):
    cm = context.client.websocket_connect("/ws", headers=headers)
    rpc = RpcWs(cm.__enter__())
    rpc.handshake(experimental=experimental)
    return cm, rpc


def drain_until(rpc, method, predicate, rounds=10):
    """Pump the socket until a matching notification has been collected.

    Terminal-landing pushes are scheduled (``ensure_future``) while the
    triggering broadcast is still in flight, so they arrive AFTER the event
    the caller was waiting on. Round-tripping a cheap RPC lets ``call``
    collect whatever the server has queued — no sleeps, no fixed ordering
    assumption.
    """
    for _ in range(rounds):
        matches = [p for p in rpc.pushed(method) if predicate(p)]
        if matches:
            return matches
        rpc.result("get_agent_info")
    return [p for p in rpc.pushed(method) if predicate(p)]


def run_turn(rpc, prompt, session_id=None):
    params = {"prompt": prompt}
    if session_id:
        params["session_id"] = session_id
    started = rpc.result("start_run", **params)
    rpc.await_run_end(started["run_id"])
    return started


# ---------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------


def test_a_client_renders_plan_status_without_parsing_prose(single_user_context):
    cm, rpc = connect(single_user_context, {})
    try:
        started = run_turn(rpc, "PLAN the work")
        plan = rpc.result("get_plan", thread_id=started["session_id"])["plan"]
        assert [(s["id"], s["subject"], s["status"]) for s in plan["steps"]] == [
            ("1", "write the module", "completed"),
            ("2", "add tests", "in_progress"),
        ]
        assert plan["counts"]["completed"] == 1
        assert plan["counts"]["in_progress"] == 1
        assert plan["plan_id"].startswith("itm_")
    finally:
        cm.__exit__(None, None, None)


def test_a_plan_survives_a_reconnect(single_user_context):
    cm, rpc = connect(single_user_context, {})
    try:
        started = run_turn(rpc, "PLAN the work")
        thread_id = started["session_id"]
    finally:
        cm.__exit__(None, None, None)

    cm2, rpc2 = connect(single_user_context, {})
    try:
        plan = rpc2.result("get_plan", thread_id=thread_id)["plan"]
        assert [s["status"] for s in plan["steps"]] == ["completed", "in_progress"]
    finally:
        cm2.__exit__(None, None, None)


def test_two_sessions_hold_independent_plans(single_user_context):
    cm, rpc = connect(single_user_context, {})
    try:
        first = run_turn(rpc, "PLAN session one")
        second = rpc.result("start_run", prompt="PLAN session two")
        rpc.await_run_end(second["run_id"])

        plan_a = rpc.result("get_plan", thread_id=first["session_id"])["plan"]
        plan_b = rpc.result("get_plan", thread_id=second["session_id"])["plan"]
        assert first["session_id"] != second["session_id"]
        assert plan_a["plan_id"] != plan_b["plan_id"]
        assert len(plan_a["steps"]) == 2
        assert len(plan_b["steps"]) == 2
    finally:
        cm.__exit__(None, None, None)


def test_plan_mutations_are_pushed_as_item_updated(single_user_context):
    cm, rpc = connect(single_user_context, {})
    try:
        run_turn(rpc, "PLAN the work")
        updates = [p for p in rpc.pushed("item_updated") if p["kind"] == "plan"]
        assert updates, "no plan item_updated notifications arrived"
        # One stable identity, monotonically advancing revisions.
        assert len({p["item_id"] for p in updates}) == 1
        revisions = [p["revision"] for p in updates]
        assert revisions == sorted(revisions)
        latest = updates[-1]
        assert latest["thread_id"] == latest["session_id"]
        assert [s["status"] for s in latest["content"]["steps"]] == [
            "completed",
            "in_progress",
        ]
        # The item's thread ordinal must not collide with the replay seq.
        assert "item_seq" in latest
        assert (
            latest["seq"] != latest["item_seq"] or latest["item_seq"] == latest["seq"]
        )
    finally:
        cm.__exit__(None, None, None)


def test_item_updated_is_replayed_after_a_disconnect(single_user_context):
    """The push is journaled, so a client that was away still converges."""
    cm, rpc = connect(single_user_context, {})
    try:
        started = run_turn(rpc, "PLAN the work")
        thread_id = started["session_id"]
    finally:
        cm.__exit__(None, None, None)

    cm2, rpc2 = connect(single_user_context, {})
    try:
        resumed = rpc2.result("resume_session", session_id=thread_id, after_seq=0)
        plan_events = [
            event
            for event in resumed["events"]
            if event["method"] == "item_updated" and event["params"]["kind"] == "plan"
        ]
        assert plan_events, "item_updated was not journaled"
        # Coalesced to the latest revision of the one plan item.
        assert len(plan_events) == 1
        assert [s["status"] for s in plan_events[0]["params"]["content"]["steps"]] == [
            "completed",
            "in_progress",
        ]
    finally:
        cm2.__exit__(None, None, None)


# ---------------------------------------------------------------------
# Run diffs
# ---------------------------------------------------------------------


def test_run_diff_is_the_net_change_of_the_run(single_user_context):
    cm, rpc = connect(single_user_context, {})
    try:
        started = run_turn(rpc, "EDIT the files")
        result = rpc.result("get_run_diff", thread_id=started["session_id"])
        run_diff = result["run_diff"]
        assert result["turn_id"] == started["run_id"]
        # Three writes to one file collapse to one net change...
        assert [f["path"] for f in run_diff["files"]] == ["mod.py"]
        assert "+v3" in run_diff["diff"]
        assert "v1" not in run_diff["diff"]
        assert "v2" not in run_diff["diff"]
        # ...and the created-then-deleted file is absent entirely.
        assert "scratch.txt" not in run_diff["diff"]
        assert run_diff["status"] == "completed"
        assert (single_user_context.workspace / "mod.py").read_text() == "v3\n"
        assert not (single_user_context.workspace / "scratch.txt").exists()
    finally:
        cm.__exit__(None, None, None)


def test_run_diff_is_pushed_after_each_edit(single_user_context):
    cm, rpc = connect(single_user_context, {})
    try:
        run_turn(rpc, "EDIT the files")
        updates = [p for p in rpc.pushed("item_updated") if p["kind"] == "run_diff"]
        assert len(updates) >= 2
        assert len({p["item_id"] for p in updates}) == 1
        assert updates[-1]["content"]["files"][0]["path"] == "mod.py"
    finally:
        cm.__exit__(None, None, None)


def test_the_settled_run_diff_is_pushed_at_run_end(single_user_context):
    """Without this push a live client's run diff shows "in progress"
    forever: the terminal landing has no notification of its own."""
    cm, rpc = connect(single_user_context, {})
    try:
        run_turn(rpc, "EDIT the files")
        settled = drain_until(
            rpc,
            "item_updated",
            lambda p: p["kind"] == "run_diff" and p["status"] == "completed",
        )
        assert settled, "the run diff settled with no push"
        updates = [p for p in rpc.pushed("item_updated") if p["kind"] == "run_diff"]
        assert updates[-1]["status"] == "completed"
        # ...and it is the same item, further revised — not a new one.
        assert len({p["item_id"] for p in updates}) == 1
        assert [p["status"] for p in updates[:-1]] == ["started"] * (len(updates) - 1)
    finally:
        cm.__exit__(None, None, None)


def test_archiving_pushes_the_landed_plan(single_user_context):
    cm, rpc = connect(single_user_context, {})
    try:
        started = run_turn(rpc, "PLAN the work")
        rpc.result("archive_session", session_id=started["session_id"])
        landed = drain_until(
            rpc,
            "item_updated",
            lambda p: p["kind"] == "plan" and p["status"] != "started",
        )
        assert landed, "the plan landed with no push"
        assert landed[-1]["status"] == "cancelled"
        assert landed[-1]["content"]["finalized_by"] == "thread_archived"
    finally:
        cm.__exit__(None, None, None)


def test_a_historical_run_exposes_its_final_diff_and_plan(single_user_context):
    cm, rpc = connect(single_user_context, {})
    try:
        first = run_turn(rpc, "EDIT the files")
        thread_id = first["session_id"]
        second = run_turn(rpc, "PLAN the follow-up", session_id=thread_id)

        # The later turn changed no files...
        assert rpc.result("get_run_diff", thread_id=thread_id)["run_diff"] is None
        # ...but the earlier one's aggregate is still addressable.
        historical = rpc.result(
            "get_run_diff", thread_id=thread_id, turn_id=first["run_id"]
        )["run_diff"]
        assert [f["path"] for f in historical["files"]] == ["mod.py"]
        assert historical["status"] == "completed"
        assert second["run_id"] != first["run_id"]
        # And the plan the thread ended with is readable.
        plan = rpc.result("get_plan", thread_id=thread_id)["plan"]
        assert [s["status"] for s in plan["steps"]] == ["completed", "in_progress"]
    finally:
        cm.__exit__(None, None, None)


def test_unknown_turn_uses_the_reserved_error_code(single_user_context):
    cm, rpc = connect(single_user_context, {})
    try:
        started = run_turn(rpc, "EDIT the files")
        error = rpc.error(
            "get_run_diff", thread_id=started["session_id"], turn_id="run_nope"
        )
        assert error["code"] == -32100
        assert error["data"] == {
            "kind": "turn_not_found",
            "thread_id": started["session_id"],
            "turn_id": "run_nope",
        }
    finally:
        cm.__exit__(None, None, None)


# ---------------------------------------------------------------------
# Authorization & negotiation
# ---------------------------------------------------------------------


def test_cross_user_plan_and_diff_reads_are_denied(context):
    alice_cm, alice = connect(context, ALICE)
    bob_cm, bob = connect(context, BOB)
    try:
        started = run_turn(alice, "PLAN alice's private work")
        thread_id = started["session_id"]

        for method, params in [
            ("get_plan", {"thread_id": thread_id}),
            ("get_plan", {"thread_id": thread_id, "scope": "main"}),
            ("get_run_diff", {"thread_id": thread_id}),
            ("get_run_diff", {"thread_id": thread_id, "turn_id": started["run_id"]}),
        ]:
            error = bob.error(method, **params)
            assert error["code"] == RPC_ERROR_AUTHORIZATION_DENIED, (method, error)
            assert error["data"]["kind"] == "authorization_denied"
            assert error["data"]["resource"] == "thread"

        # Alice still reads her own.
        assert alice.result("get_plan", thread_id=thread_id)["plan"] is not None
    finally:
        bob_cm.__exit__(None, None, None)
        alice_cm.__exit__(None, None, None)


def test_experimental_operations_must_be_negotiated(single_user_context):
    cm, rpc = connect(single_user_context, {}, experimental=())
    try:
        started = rpc.result("start_run", prompt="PLAN the work")
        rpc.await_run_end(started["run_id"])
        error = rpc.error("get_plan", thread_id=started["session_id"])
        assert error["code"] == -32013
        assert error["data"]["kind"] == "capability_not_negotiated"
        # And the notification is withheld from a client that did not opt in.
        assert rpc.pushed("item_updated") == []
    finally:
        cm.__exit__(None, None, None)


def test_the_read_deadline_shouts_if_it_ever_stops_working(single_user_context):
    """The deadline is a mitigation, and mitigations that can silently stop
    working are the ones that do. If a future Starlette renames the
    internals this reaches for, the fallback must be loud rather than
    quietly restoring the hang."""

    class _RenamedInternals:
        """A session whose deadline hooks have been renamed away, but which
        still works through Starlette's own public ``receive_json``."""

        portal = None
        _send_rx = None

        def __init__(self, ws):
            self._ws = ws

        def __getattr__(self, name):
            return getattr(self._ws, name)

    cm, rpc = connect(single_user_context, {})
    try:
        rpc.ws = _RenamedInternals(rpc.ws)
        with pytest.warns(RuntimeWarning, match="read deadline unavailable"):
            rpc.result("get_agent_info")
    finally:
        cm.__exit__(None, None, None)
