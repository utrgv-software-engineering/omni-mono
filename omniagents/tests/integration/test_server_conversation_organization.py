"""Conversation organization, search & branching over the real ``/ws``.

The wire contract for tkt_eZldW-Gje6kn: listings page a genuinely large
set, search identifies conversations with previews, forks branch from a
completed turn, the descendant policy holds, metadata changes are
observable, and every new method refuses a second principal with -32014.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agents import Agent
from omniagents.backends.server import app as server_app
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.runtime import bridge as runner_bridge
from omniagents.rpc.auth import RPC_ERROR_AUTHORIZATION_DENIED

ALICE = {"Authorization": "Bearer alice-token"}
BOB = {"Authorization": "Bearer bob-token"}

ORGANIZATION_OPERATIONS = (
    "list_threads",
    "search_threads",
    "update_thread",
    "list_thread_descendants",
    "export_thread",
    "purge_threads",
    "fork_session",
    "session_forked",
    "thread_updated",
)


async def mock_build_agent(settings, mcp, app):
    return Agent(name="org_agent", tools=[], instructions="Org", model="gpt-3.5-turbo")


async def mock_stream_agent(agent, prompt, sess, channel, max_turns=20, **kwargs):
    """One turn, driven entirely through the notification chokepoint."""
    from omniagents.core.runtime.bridge import _broadcast
    from omniagents.core.session import append_message

    run_id = asyncio.current_task().get_name()
    user_msg = {"role": "user", "content": prompt}
    sess.history.append(user_msg)
    append_message(sess.id, user_msg)

    await _broadcast(
        sess,
        "tool_called",
        {
            "run_id": run_id,
            "session_id": sess.id,
            "tool": "read_file",
            "input": '{"path":"pipeline.py"}',
            "call_id": f"{run_id}-c1",
        },
    )
    await _broadcast(
        sess,
        "tool_result",
        {
            "run_id": run_id,
            "session_id": sess.id,
            "tool": "read_file",
            "output": f"contents for {prompt}",
            "call_id": f"{run_id}-c1",
        },
    )
    await _broadcast(
        sess,
        "message_output",
        {"run_id": run_id, "session_id": sess.id, "content": f"handled: {prompt}"},
    )
    reply = {"role": "assistant", "content": f"handled: {prompt}"}
    sess.history.append(reply)
    append_message(sess.id, reply)


class RpcWs:
    def __init__(self, ws):
        self.ws = ws
        self.next_id = 0
        self.notifications = []

    def call(self, method, **params):
        self.next_id += 1
        call_id = f"c{self.next_id}"
        self.ws.send_json(
            {"jsonrpc": "2.0", "id": call_id, "method": method, "params": params}
        )
        while True:
            message = self.ws.receive_json()
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

    def await_run_end(self, run_id, limit=200):
        for _ in range(limit):
            message = self.ws.receive_json()
            self.notifications.append(message)
            if (
                message.get("method") == "run_end"
                and message.get("params", {}).get("run_id") == run_id
            ):
                return message["params"]
        raise AssertionError(f"run_end for {run_id} never arrived")

    def handshake(self, experimental=ORGANIZATION_OPERATIONS):
        self.result(
            "initialize",
            protocol_version="1.0.0",
            identity={"name": "organization-tests", "version": "0.0.0"},
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


def _spec(name, **kwargs):
    return AgentSpec(
        name=name,
        welcome_text="Org",
        get_agent_instructions=lambda: "Org",
        build_agent=mock_build_agent,
        **kwargs,
    )


@pytest.fixture
def context(monkeypatch):
    from omniagents.core.agents import service as service_mod

    monkeypatch.setattr(runner_bridge, "stream_agent", mock_stream_agent)
    monkeypatch.setattr(service_mod, "stream_agent", mock_stream_agent)
    monkeypatch.delenv("OMNIAGENTS_LEGACY_QUERY_AUTH", raising=False)

    spec = _spec(
        "OrgAgent",
        security_config={
            "providers": {
                "auth": {
                    "type": "token",
                    "tokens": {"alice-token": "alice", "bob-token": "bob"},
                }
            }
        },
    )
    app = server_app.build_app(spec=spec, default_host_workspace_root="/tmp")
    client = TestClient(app)
    yield SimpleNamespace(client=client, service=app.state.agent_service)


@pytest.fixture
def single_user_context(monkeypatch):
    from omniagents.core.agents import service as service_mod

    monkeypatch.setattr(runner_bridge, "stream_agent", mock_stream_agent)
    monkeypatch.setattr(service_mod, "stream_agent", mock_stream_agent)
    app = server_app.build_app(
        spec=_spec("OrgLocal"), default_host_workspace_root="/tmp"
    )
    client = TestClient(app)
    yield SimpleNamespace(client=client, service=app.state.agent_service)


def connect(context, headers=None, experimental=ORGANIZATION_OPERATIONS):
    cm = context.client.websocket_connect("/ws", headers=headers or {})
    rpc = RpcWs(cm.__enter__())
    rpc.handshake(experimental=experimental)
    return cm, rpc


def run_turn(rpc, prompt, session_id=None):
    params = {"prompt": prompt}
    if session_id:
        params["session_id"] = session_id
    started = rpc.result("start_run", **params)
    rpc.await_run_end(started["run_id"])
    return started


# ---------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------


def test_a_large_history_loads_incrementally(single_user_context):
    """A genuinely large synthetic set, paged by cursor."""
    service = single_user_context.service
    store = service._conversation_service().store
    from omniagents.core.conversation.model import Thread

    for index in range(250):
        store.upsert_thread(
            Thread(
                thread_id=f"bulk-{index:04d}",
                title=f"Conversation {index}",
                created_at=1000.0 + index,
                updated_at=1000.0 + index,
            )
        )

    cm, rpc = connect(single_user_context)
    try:
        seen = []
        cursor = None
        pages = 0
        while True:
            page = rpc.result(
                "list_threads", limit=40, **({"cursor": cursor} if cursor else {})
            )
            assert page["total"] == 250
            seen.extend(thread["thread_id"] for thread in page["threads"])
            pages += 1
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]
        assert pages >= 7, "the whole set came back in one page"
        assert len(seen) == 250
        assert len(set(seen)) == 250
        assert seen == sorted(seen, reverse=True)
    finally:
        cm.__exit__(None, None, None)


def test_listing_carries_live_state_and_filters(single_user_context):
    cm, rpc = connect(single_user_context)
    try:
        started = run_turn(rpc, "first task")
        thread_id = started["session_id"]
        rpc.result("update_thread", thread_id=thread_id, pinned=True)

        page = rpc.result("list_threads", pinned=True)
        assert [t["thread_id"] for t in page["threads"]] == [thread_id]
        entry = page["threads"][0]
        assert entry["pinned"] is True
        assert entry["loaded"] is True
        assert entry["active"] is False
        assert entry["pending_attention"] == 0
        assert entry["attention"]["tool_approvals"] == 0

        assert rpc.result("list_threads", pinned=False)["threads"] == []
        assert rpc.result("list_threads", status="archived")["threads"] == []
        assert (
            rpc.result("list_threads", status="active")["threads"][0]["thread_id"]
            == thread_id
        )
    finally:
        cm.__exit__(None, None, None)


def test_an_invalid_listing_cursor_is_rejected(single_user_context):
    cm, rpc = connect(single_user_context)
    try:
        run_turn(rpc, "task")
        error = rpc.error("list_threads", cursor="not-a-cursor")
        assert error["code"] == -32082
        assert error["data"]["kind"] == "cursor_invalid"
    finally:
        cm.__exit__(None, None, None)


# ---------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------


def test_search_identifies_conversations_with_previews(single_user_context):
    cm, rpc = connect(single_user_context)
    try:
        first = run_turn(rpc, "rewrite the tokenizer")
        run_turn(rpc, "unrelated errand")

        found = rpc.result("search_threads", query="tokenizer")
        assert found["total"] == 1
        [hit] = found["results"]
        assert hit["thread_id"] == first["session_id"]
        assert hit["match_count"] >= 1
        assert "tokenizer" in hit["preview"]
        assert hit["thread"]["thread_id"] == first["session_id"]
        assert hit["thread"]["loaded"] is True

        # Tool payloads are indexed, not only messages.
        tools = rpc.result("search_threads", query="pipeline")
        assert tools["total"] == 2
        assert all(h["match_kind"] == "tool_call" for h in tools["results"])
    finally:
        cm.__exit__(None, None, None)


def test_search_survives_backend_query_syntax(single_user_context):
    cm, rpc = connect(single_user_context)
    try:
        run_turn(rpc, "quokka sighting")
        for hostile in ('"unterminated', "a & b", "NEAR(x y)", "*", "'"):
            result = rpc.result("search_threads", query=hostile)
            assert isinstance(result["total"], int)
        assert rpc.result("search_threads", query="   ")["results"] == []
    finally:
        cm.__exit__(None, None, None)


# ---------------------------------------------------------------------
# Branching
# ---------------------------------------------------------------------


def test_forking_a_completed_turn_branches_with_correct_content(single_user_context):
    cm, rpc = connect(single_user_context)
    try:
        first = run_turn(rpc, "turn one")
        thread_id = first["session_id"]
        run_turn(rpc, "turn two", session_id=thread_id)
        run_turn(rpc, "turn three", session_id=thread_id)

        parent_items = rpc.result("list_items", thread_id=thread_id, limit=500)["items"]
        turn_two_last = max(
            item["seq"] for item in parent_items if item["turn_id"] == first["run_id"]
        )

        forked = rpc.result(
            "fork_session", session_id=thread_id, from_turn_id=first["run_id"]
        )
        child_id = forked["new_session_id"]

        child = rpc.result("get_thread", thread_id=child_id)
        assert child["parent_thread_id"] == thread_id
        assert child["branched_from_item_id"] == forked["branched_from_item_id"]

        child_items = rpc.result("list_items", thread_id=child_id, limit=500)["items"]
        assert [i["seq"] for i in child_items] == list(range(1, turn_two_last + 1))
        assert [i["content"] for i in child_items] == [
            i["content"] for i in parent_items[:turn_two_last]
        ]
        assert child["item_count"] == turn_two_last
        assert len(parent_items) > turn_two_last

        turns = rpc.result("list_turns", thread_id=child_id)["turns"]
        assert [t["turn_id"] for t in turns] == [first["run_id"]]
    finally:
        cm.__exit__(None, None, None)


def test_the_fork_notification_carries_the_branch_point(single_user_context):
    """``session_forked`` is a materialization path in its own right — a
    consumer that only sees the notification (a client rendering the branch,
    a recorder in another process) has to be told WHERE the fork branched.
    A payload without it means "the whole transcript"."""
    cm, rpc = connect(single_user_context)
    try:
        started = run_turn(rpc, "trunk")
        thread_id = started["session_id"]

        plain = rpc.result("fork_session", session_id=thread_id)
        forked = [
            note for note in rpc.notifications if note.get("method") == "session_forked"
        ]
        assert forked, "session_forked was never broadcast"
        assert forked[-1]["params"]["new_session_id"] == plain["new_session_id"]
        assert forked[-1]["params"].get("from_item_id") is None

        branched = rpc.result(
            "fork_session", session_id=thread_id, from_turn_id=started["run_id"]
        )
        forked = [
            note
            for note in rpc.notifications
            if note.get("method") == "session_forked"
            and note["params"]["new_session_id"] == branched["new_session_id"]
        ]
        assert forked, "the branched fork was never broadcast"
        assert forked[-1]["params"]["from_item_id"] == branched["branched_from_item_id"]
    finally:
        cm.__exit__(None, None, None)


def test_descendants_expose_the_branch_relationship(single_user_context):
    cm, rpc = connect(single_user_context)
    try:
        first = run_turn(rpc, "trunk")
        thread_id = first["session_id"]
        child = rpc.result("fork_session", session_id=thread_id)["new_session_id"]
        grand = rpc.result("fork_session", session_id=child)["new_session_id"]

        tree = rpc.result("list_thread_descendants", thread_id=thread_id)
        assert tree["parent_thread_id"] is None
        assert [(d["thread_id"], d["depth"]) for d in tree["descendants"]] == [
            (child, 1),
            (grand, 2),
        ]
        assert tree["truncated"] is False
        assert all("loaded" in d for d in tree["descendants"])

        shallow = rpc.result(
            "list_thread_descendants", thread_id=thread_id, max_depth=1
        )
        assert [d["thread_id"] for d in shallow["descendants"]] == [child]

        upward = rpc.result("list_thread_descendants", thread_id=grand)
        assert upward["parent_thread_id"] == child
        assert upward["descendants"] == []
    finally:
        cm.__exit__(None, None, None)


def test_supported_forks_preserve_one_owner_through_the_whole_tree(context):
    """The supported fork primitive cannot manufacture mixed-owner ancestry."""
    from omniagents.core.providers.auth.base import UserIdentity
    from omniagents.rpc.auth import AuthorizationDenied

    manager = context.service._session_manager
    root = manager.get_or_create("owner-root", user_id="alice")
    child = manager.fork_session(root, new_session_id="owner-child")
    grandchild = manager.fork_session(child, new_session_id="owner-grandchild")

    assert [session.user_id for session in (root, child, grandchild)] == [
        "alice",
        "alice",
        "alice",
    ]

    async def check_cross_owner_denial():
        bob = UserIdentity(user_id="bob")
        for session in (root, child, grandchild):
            with pytest.raises(AuthorizationDenied):
                await context.service.authorize_rpc(
                    bob, "fork_session", {"session_id": session.id}
                )

    asyncio.run(check_cross_owner_denial())


def test_forking_a_running_turn_is_refused(single_user_context):
    cm, rpc = connect(single_user_context)
    try:
        started = run_turn(rpc, "settled")
        thread_id = started["session_id"]
        error = rpc.error(
            "fork_session", session_id=thread_id, from_turn_id="run-does-not-exist"
        )
        assert error["code"] == -32112
        assert error["data"]["kind"] == "fork_point_invalid"

        missing = rpc.error(
            "fork_session", session_id=thread_id, from_item_id="itm_nope"
        )
        assert missing["code"] == -32081
    finally:
        cm.__exit__(None, None, None)


# ---------------------------------------------------------------------
# Descendant policy
# ---------------------------------------------------------------------


def test_deleting_a_forked_parent_is_refused_then_cascades(single_user_context):
    cm, rpc = connect(single_user_context)
    try:
        started = run_turn(rpc, "trunk")
        thread_id = started["session_id"]
        child = rpc.result("fork_session", session_id=thread_id)["new_session_id"]

        error = rpc.error("delete_session", session_id=thread_id)
        assert error["code"] == -32111
        assert error["data"]["kind"] == "thread_has_descendants"
        assert error["data"]["descendant_thread_ids"] == [child]
        # The refusal destroyed nothing.
        assert rpc.result("get_thread", thread_id=thread_id)["thread_id"] == thread_id
        assert rpc.result("get_thread", thread_id=child)["thread_id"] == child

        assert rpc.result("delete_session", session_id=thread_id, cascade=True) is True
        assert rpc.error("get_thread", thread_id=thread_id)["code"] == -32080
        assert rpc.error("get_thread", thread_id=child)["code"] == -32080
    finally:
        cm.__exit__(None, None, None)


def test_archiving_cascades_over_the_wire(single_user_context):
    cm, rpc = connect(single_user_context)
    try:
        started = run_turn(rpc, "trunk")
        thread_id = started["session_id"]
        child = rpc.result("fork_session", session_id=thread_id)["new_session_id"]

        result = rpc.result("update_thread", thread_id=thread_id, status="archived")
        assert "status" in result["changed"]
        assert result["cascaded_thread_ids"] == [child]
        assert rpc.result("get_thread", thread_id=child)["status"] == "archived"

        archived = rpc.result("list_threads", status="archived")
        assert {t["thread_id"] for t in archived["threads"]} == {thread_id, child}
    finally:
        cm.__exit__(None, None, None)


# ---------------------------------------------------------------------
# Metadata + notification
# ---------------------------------------------------------------------


def test_metadata_changes_reach_another_connection(single_user_context):
    """Multi-column consistency: the *other* connection is the one that
    needs telling, so the notification is connection-wide."""
    watcher_cm, watcher = connect(single_user_context)
    actor_cm, actor = connect(single_user_context)
    try:
        started = run_turn(actor, "trunk")
        thread_id = started["session_id"]

        actor.result("update_thread", thread_id=thread_id, title="Renamed", pinned=True)

        for _ in range(50):
            message = watcher.ws.receive_json()
            watcher.notifications.append(message)
            if message.get("method") == "thread_updated":
                break
        else:  # pragma: no cover - defensive
            raise AssertionError("thread_updated never arrived on the other socket")

        params = message["params"]
        assert params["thread_id"] == thread_id
        assert sorted(params["changed"]) == ["pinned", "title"]
        assert params["thread"]["title"] == "Renamed"
        assert params["thread"]["pinned"] is True
    finally:
        actor_cm.__exit__(None, None, None)
        watcher_cm.__exit__(None, None, None)


def test_a_thread_update_is_not_broadcast_to_another_principal(context):
    """The notification carries a title derived from the user's own words;
    a connection-wide fan-out has to be filtered per recipient."""
    bob_cm, bob = connect(context, BOB)
    alice_cm, alice = connect(context, ALICE)
    try:
        started = run_turn(alice, "alice private matter")
        thread_id = started["session_id"]
        alice.result("update_thread", thread_id=thread_id, title="Alice secret plan")

        # Alice's own socket sees it. The broadcast happens before the
        # response, so it is already in the notifications this connection
        # collected while waiting for the result.
        owned = [
            note
            for note in alice.notifications
            if note.get("method") == "thread_updated"
        ]
        assert owned, "owner never received thread_updated"
        assert owned[-1]["params"]["thread"]["title"] == "Alice secret plan"

        # …and Bob's does not. Prove the socket is alive by round-tripping
        # a request through it and checking nothing leaked on the way.
        bob.result("list_threads")
        leaked = [
            note for note in bob.notifications if note.get("method") == "thread_updated"
        ]
        assert leaked == [], leaked
    finally:
        alice_cm.__exit__(None, None, None)
        bob_cm.__exit__(None, None, None)


# ---------------------------------------------------------------------
# Export + retention
# ---------------------------------------------------------------------


def test_export_returns_the_whole_conversation(single_user_context):
    cm, rpc = connect(single_user_context)
    try:
        started = run_turn(rpc, "export me")
        thread_id = started["session_id"]
        expected = [
            item["item_id"]
            for item in rpc.result("list_items", thread_id=thread_id, limit=500)[
                "items"
            ]
        ]

        seen = []
        cursor = None
        while True:
            page = rpc.result(
                "export_thread",
                thread_id=thread_id,
                limit=2,
                **({"cursor": cursor} if cursor else {}),
            )
            seen.extend(item["item_id"] for item in page["items"])
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]
        assert seen == expected
        assert len(seen) >= 3
    finally:
        cm.__exit__(None, None, None)


def test_retention_purge_is_refused_for_an_identified_principal(context):
    cm, rpc = connect(context, ALICE)
    try:
        error = rpc.error("purge_threads", retention_days=30)
        assert error["code"] == RPC_ERROR_AUTHORIZATION_DENIED
        assert error["data"]["kind"] == "authorization_denied"
        assert error["data"]["resource"] == "global"
    finally:
        cm.__exit__(None, None, None)


def test_retention_purge_runs_in_single_user_mode(single_user_context):
    cm, rpc = connect(single_user_context)
    try:
        run_turn(rpc, "recent work")
        result = rpc.result("purge_threads", retention_days=30, dry_run=True)
        assert result["dry_run"] is True
        assert result["purged_thread_ids"] == []
        assert rpc.result("purge_threads", retention_days=0)["count"] == 0
    finally:
        cm.__exit__(None, None, None)


# ---------------------------------------------------------------------
# Authorization: cross-user probes for every new method
# ---------------------------------------------------------------------


def test_every_thread_scoped_method_refuses_another_principal(context):
    alice_cm, alice = connect(context, ALICE)
    bob_cm, bob = connect(context, BOB)
    try:
        started = run_turn(alice, "alice work")
        thread_id = started["session_id"]
        child = alice.result("fork_session", session_id=thread_id)["new_session_id"]

        probes = [
            ("update_thread", {"thread_id": thread_id, "title": "stolen"}),
            ("list_thread_descendants", {"thread_id": thread_id}),
            ("export_thread", {"thread_id": thread_id}),
            ("get_thread", {"thread_id": thread_id}),
            ("fork_session", {"session_id": thread_id}),
            ("delete_session", {"session_id": thread_id, "cascade": True}),
        ]
        for method, params in probes:
            error = bob.error(method, **params)
            assert error["code"] == RPC_ERROR_AUTHORIZATION_DENIED, (method, error)
            assert error["data"]["kind"] == "authorization_denied"

        # Nothing Bob attempted took effect.
        assert alice.result("get_thread", thread_id=thread_id)["title"] != "stolen"
        assert alice.result("get_thread", thread_id=child)["thread_id"] == child
    finally:
        bob_cm.__exit__(None, None, None)
        alice_cm.__exit__(None, None, None)


def test_listings_and_search_never_show_another_principal_s_threads(context):
    alice_cm, alice = connect(context, ALICE)
    bob_cm, bob = connect(context, BOB)
    try:
        started = run_turn(alice, "alice confidential codeword zebra")
        alice.result("update_thread", thread_id=started["session_id"], title="Zebra")
        run_turn(bob, "bob ordinary errand")

        alice_ids = {t["thread_id"] for t in alice.result("list_threads")["threads"]}
        bob_ids = {t["thread_id"] for t in bob.result("list_threads")["threads"]}
        assert started["session_id"] in alice_ids
        assert started["session_id"] not in bob_ids
        assert not (alice_ids & bob_ids)

        assert alice.result("search_threads", query="zebra")["total"] == 1
        bob_search = bob.result("search_threads", query="zebra")
        assert bob_search["total"] == 0
        assert bob_search["results"] == []
    finally:
        bob_cm.__exit__(None, None, None)
        alice_cm.__exit__(None, None, None)


def test_the_organization_methods_require_the_experimental_grant(single_user_context):
    cm, rpc = connect(single_user_context, experimental=())
    try:
        for method, params in [
            ("list_threads", {}),
            ("search_threads", {"query": "x"}),
            ("update_thread", {"thread_id": "t"}),
            ("list_thread_descendants", {"thread_id": "t"}),
            ("export_thread", {"thread_id": "t"}),
            ("purge_threads", {"retention_days": 1}),
        ]:
            error = rpc.error(method, **params)
            assert error["code"] == -32013, (method, error)
            assert error["data"]["capability"] == method
    finally:
        cm.__exit__(None, None, None)


# ---------------------------------------------------------------------
# Live state: "preserve loaded/active status and pending-attention
# indicators" is named ticket scope, so each of the three is asserted in
# BOTH of its states.
# ---------------------------------------------------------------------


def test_pending_attention_counts_a_real_waiting_approval(single_user_context):
    from omniagents.core.runtime.bridge import _broadcast

    cm, rpc = connect(single_user_context)
    try:
        started = run_turn(rpc, "needs approval")
        thread_id = started["session_id"]
        before = rpc.result("list_threads")["threads"][0]
        assert before["pending_attention"] == 0
        assert before["attention"]["tool_approvals"] == 0

        sess = single_user_context.service.get_session(thread_id)
        sess.pending_tool_approvals["call-1"] = {"tool_name": "execute_bash"}
        sess.pending_elicitations["elic-1"] = {"message": "which branch?"}

        after = rpc.result("list_threads")["threads"][0]
        assert after["pending_attention"] == 2
        assert after["attention"]["tool_approvals"] == 1
        assert after["attention"]["elicitations"] == 1
        assert after["attention"]["mcp_approvals"] == 0
        _ = _broadcast
    finally:
        cm.__exit__(None, None, None)


def test_active_is_true_while_a_run_is_in_flight(single_user_context):
    """``active`` has to reflect the live task, not a constant."""
    import asyncio

    cm, rpc = connect(single_user_context)
    try:
        started = run_turn(rpc, "first")
        thread_id = started["session_id"]
        assert rpc.result("list_threads")["threads"][0]["active"] is False

        sess = single_user_context.service.get_session(thread_id)

        async def forever():
            await asyncio.sleep(3600)

        loop = asyncio.new_event_loop()
        task = loop.create_task(forever())
        sess.current_task = task
        try:
            assert rpc.result("list_threads")["threads"][0]["active"] is True
        finally:
            task.cancel()
            sess.current_task = None
            loop.close()
        assert rpc.result("list_threads")["threads"][0]["active"] is False
    finally:
        cm.__exit__(None, None, None)


def test_loaded_is_false_for_a_stored_but_unloaded_thread(single_user_context):
    cm, rpc = connect(single_user_context)
    try:
        started = run_turn(rpc, "will be dropped")
        thread_id = started["session_id"]
        assert rpc.result("list_threads")["threads"][0]["loaded"] is True

        # Forget the live Session; the thread row stays.
        single_user_context.service._session_manager.drop(thread_id)

        entry = rpc.result("list_threads")["threads"][0]
        assert entry["thread_id"] == thread_id
        assert entry["loaded"] is False
        assert entry["active"] is False
        assert entry["pending_attention"] == 0
    finally:
        cm.__exit__(None, None, None)


def test_listing_counts_are_recounted_not_remembered(single_user_context):
    """The listing is the sidebar, and the stored counters drift."""
    cm, rpc = connect(single_user_context)
    try:
        started = run_turn(rpc, "counted")
        thread_id = started["session_id"]
        exact = rpc.result("get_thread", thread_id=thread_id)

        store = single_user_context.service._conversation_service().store
        drifted = store.get_thread(thread_id)
        drifted.item_count = 999
        drifted.turn_count = 77
        store.upsert_thread(drifted)

        entry = rpc.result("list_threads")["threads"][0]
        assert entry["item_count"] == exact["item_count"]
        assert entry["turn_count"] == exact["turn_count"]
        assert entry["item_count"] != 999

        hit = rpc.result("search_threads", query="counted")["results"][0]
        assert hit["thread"]["item_count"] == exact["item_count"]

        updated = rpc.result("update_thread", thread_id=thread_id, pinned=True)
        assert updated["thread"]["item_count"] == exact["item_count"]
    finally:
        cm.__exit__(None, None, None)


# ---------------------------------------------------------------------
# archive_session is the STABLE path shipped clients call
# ---------------------------------------------------------------------


def test_archive_session_broadcasts_to_other_connections(single_user_context):
    watcher_cm, watcher = connect(single_user_context)
    actor_cm, actor = connect(single_user_context)
    try:
        started = run_turn(actor, "trunk")
        thread_id = started["session_id"]
        child = actor.result("fork_session", session_id=thread_id)["new_session_id"]

        assert actor.result("archive_session", session_id=thread_id) is True

        for _ in range(80):
            message = watcher.ws.receive_json()
            watcher.notifications.append(message)
            if message.get("method") == "thread_updated":
                break
        else:  # pragma: no cover - defensive
            raise AssertionError(
                "archive_session did not reach the other connection, so a "
                "second column keeps showing an archived conversation"
            )
        params = message["params"]
        assert params["thread_id"] == thread_id
        assert params["changed"] == ["status"]
        assert params["thread"]["status"] == "archived"
        assert params["cascaded_thread_ids"] == [child]
    finally:
        actor_cm.__exit__(None, None, None)
        watcher_cm.__exit__(None, None, None)


# ---------------------------------------------------------------------
# Delete: the descendant guard over the wire, unbounded and owner-blind
# ---------------------------------------------------------------------


def test_a_foreign_owned_fork_blocks_a_plain_delete_over_the_wire(context):
    from omniagents.core.conversation.model import Thread

    store = context.service._conversation_service().store
    alice_cm, alice = connect(context, ALICE)
    try:
        started = run_turn(alice, "alice trunk")
        thread_id = started["session_id"]
        store.upsert_thread(
            Thread(
                thread_id="bob-fork",
                user_id="bob",
                parent_thread_id=thread_id,
                created_at=9.0,
                updated_at=9.0,
            )
        )
        error = alice.error("delete_session", session_id=thread_id)
        assert error["code"] == -32111
        # The COUNT is exact — the guard must not miss a descendant it
        # cannot read — but the id list names only what Alice may read, so
        # a refusal is not a side channel for Bob's thread ids.
        assert error["data"]["descendant_count"] == 1
        assert error["data"]["descendant_thread_ids"] == []
        assert error["data"]["truncated"] is True
        assert "bob-fork" not in str(error)

        # …and a cascade is refused rather than deleting another
        # principal's conversation — again without naming it.
        denied = alice.error("delete_session", session_id=thread_id, cascade=True)
        assert denied["code"] == RPC_ERROR_AUTHORIZATION_DENIED
        assert denied["data"]["resource_id"] == thread_id
        assert "bob-fork" not in str(denied)
        assert store.get_thread("bob-fork") is not None
        assert store.get_thread(thread_id) is not None
    finally:
        alice_cm.__exit__(None, None, None)


def test_archiving_does_not_cascade_across_principals(context):
    """The archive cascade rides the same owner-agnostic walk the delete
    guard does, so it needs its own access decision. Without one it writes
    another principal's ``Thread.status``, their ``sessions.archived`` row
    and their live ``Session.archived`` — through the STABLE path."""
    from omniagents.core.conversation.model import Thread, ThreadStatus
    from omniagents.core.session import history_db

    store = context.service._conversation_service().store
    alice_cm, alice = connect(context, ALICE)
    try:
        started = run_turn(alice, "alice trunk")
        thread_id = started["session_id"]
        mine = alice.result("fork_session", session_id=thread_id)["new_session_id"]
        for foreign_id, owner in (("bob-fork", "bob"), ("orphan-fork", None)):
            store.upsert_thread(
                Thread(
                    thread_id=foreign_id,
                    user_id=owner,
                    parent_thread_id=thread_id,
                    created_at=9.0,
                    updated_at=9.0,
                )
            )

        # The STABLE path.
        assert alice.result("archive_session", session_id=thread_id) is True
        assert store.get_thread(thread_id).status is ThreadStatus.ARCHIVED
        assert store.get_thread(mine).status is ThreadStatus.ARCHIVED
        for foreign_id in ("bob-fork", "orphan-fork"):
            assert (
                store.get_thread(foreign_id).status is ThreadStatus.ACTIVE
            ), f"{foreign_id} was archived by another principal's cascade"
            assert history_db.get_archived(foreign_id) is False

        # …and the experimental one.
        alice.result("update_thread", thread_id=thread_id, status="active")
        result = alice.result("update_thread", thread_id=thread_id, status="archived")
        assert result["cascaded_thread_ids"] == [mine]
        assert result["inaccessible_descendant_count"] == 2
        assert "bob-fork" not in str(result)
        for foreign_id in ("bob-fork", "orphan-fork"):
            assert store.get_thread(foreign_id).status is ThreadStatus.ACTIVE
    finally:
        alice_cm.__exit__(None, None, None)


def test_a_foreign_fork_owner_is_not_told_about_the_archive(context):
    """The notification is the other half of the same fix: Bob must not
    receive a ``thread_updated`` for a thread that did not change."""
    from omniagents.core.conversation.model import Thread

    store = context.service._conversation_service().store
    bob_cm, bob = connect(context, BOB)
    alice_cm, alice = connect(context, ALICE)
    try:
        started = run_turn(alice, "alice trunk")
        thread_id = started["session_id"]
        store.upsert_thread(
            Thread(
                thread_id="bob-fork",
                user_id="bob",
                parent_thread_id=thread_id,
                created_at=9.0,
                updated_at=9.0,
            )
        )
        alice.result("archive_session", session_id=thread_id)

        bob.result("list_threads")
        leaked = [
            note for note in bob.notifications if note.get("method") == "thread_updated"
        ]
        assert leaked == [], leaked
    finally:
        alice_cm.__exit__(None, None, None)
        bob_cm.__exit__(None, None, None)


def test_a_held_session_survives_a_cascade_attempt(single_user_context):
    from omniagents.core.session import history_db

    store = single_user_context.service._conversation_service().store
    cm, rpc = connect(
        single_user_context, experimental=(*ORGANIZATION_OPERATIONS, "set_session_hold")
    )
    try:
        started = run_turn(rpc, "held trunk")
        thread_id = started["session_id"]
        child = rpc.result("fork_session", session_id=thread_id)["new_session_id"]
        assert history_db.set_session_hold(thread_id, True) is True

        error = rpc.error("delete_session", session_id=thread_id, cascade=True)
        assert error["code"] == -32113
        assert error["data"]["kind"] == "thread_on_legal_hold"
        assert error["data"]["held_thread_ids"] == [thread_id]
        assert store.get_thread(thread_id) is not None
        assert store.get_thread(child) is not None
    finally:
        cm.__exit__(None, None, None)


def test_oversized_metadata_is_refused_over_the_wire(single_user_context):
    from omniagents.core.conversation.service import (
        METADATA_MAX_BYTES,
        METADATA_MAX_KEYS,
    )

    cm, rpc = connect(single_user_context)
    try:
        started = run_turn(rpc, "meta")
        thread_id = started["session_id"]
        error = rpc.error(
            "update_thread",
            thread_id=thread_id,
            metadata={"blob": "x" * (METADATA_MAX_BYTES + 10)},
        )
        assert error["code"] == -32114
        assert error["data"]["kind"] == "thread_metadata_invalid"

        keys = rpc.error(
            "update_thread",
            thread_id=thread_id,
            metadata={f"k{i}": i for i in range(METADATA_MAX_KEYS + 1)},
        )
        assert keys["code"] == -32114
        assert rpc.result("get_thread", thread_id=thread_id)["metadata"] == {}
    finally:
        cm.__exit__(None, None, None)


def test_a_null_title_clears_it_over_the_wire(single_user_context):
    """The schema now admits ``null`` for the documented clear signal."""
    cm, rpc = connect(single_user_context)
    try:
        started = run_turn(rpc, "titled")
        thread_id = started["session_id"]
        rpc.result("update_thread", thread_id=thread_id, title="Renamed")
        assert rpc.result("get_thread", thread_id=thread_id)["title"] == "Renamed"

        # Omitting it leaves it alone…
        rpc.result("update_thread", thread_id=thread_id, pinned=True)
        assert rpc.result("get_thread", thread_id=thread_id)["title"] == "Renamed"

        # …and an explicit null clears it.
        cleared = rpc.result("update_thread", thread_id=thread_id, title=None)
        assert cleared["changed"] == ["title"]
        assert rpc.result("get_thread", thread_id=thread_id)["title"] is None
    finally:
        cm.__exit__(None, None, None)
