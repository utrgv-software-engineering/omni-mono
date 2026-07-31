"""The conversation domain surface over the real ``/ws`` endpoint.

Covers the protocol contract end to end: a run drives the recorder through
the notification chokepoint, the reads come back paginated with canonical
ids, typed errors surface with the reserved codes, and a second principal
is refused with ``-32014``.
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


async def mock_build_agent(settings, mcp, app):
    return Agent(
        name="conv_agent", tools=[], instructions="Conv", model="gpt-3.5-turbo"
    )


async def mock_stream_agent(agent, prompt, sess, channel, max_turns=20, **kwargs):
    """Emit the notification sequence a real coding turn produces.

    Everything flows through ``_broadcast``, i.e. the same
    ``stamp_session_event`` chokepoint the recorder hooks — so this drives
    the production write path, not a test-only shortcut.
    """
    from omniagents.core.runtime.bridge import _broadcast

    run_id = asyncio.current_task().get_name()
    user_msg = {"role": "user", "content": prompt}
    sess.history.append(user_msg)
    from omniagents.core.session import append_message

    append_message(sess.id, user_msg)

    await _broadcast(
        sess,
        "tool_called",
        {
            "run_id": run_id,
            "session_id": sess.id,
            "tool": "read_file",
            "input": '{"path":"a.py"}',
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
            "output": "def f(): ...",
            "call_id": f"{run_id}-c1",
        },
    )
    await _broadcast(
        sess,
        "tool_called",
        {
            "run_id": run_id,
            "session_id": sess.id,
            "tool": "mcp_docs__search",
            "input": "{}",
            "call_id": f"{run_id}-c2",
            "server_label": "docs",
            "tool_label": "search",
        },
    )
    await _broadcast(
        sess,
        "tool_result",
        {
            "run_id": run_id,
            "session_id": sess.id,
            "tool": "mcp_docs__search",
            "output": "hits",
            "call_id": f"{run_id}-c2",
            "server_label": "docs",
        },
    )
    await _broadcast(
        sess,
        "message_output",
        {"run_id": run_id, "session_id": sess.id, "content": f"handled: {prompt}"},
    )


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

    def handshake(self, experimental=()):
        self.result(
            "initialize",
            protocol_version="1.0.0",
            identity={"name": "conversation-tests", "version": "0.0.0"},
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


@pytest.fixture
def context(monkeypatch):
    from omniagents.core.agents import service as service_mod

    monkeypatch.setattr(runner_bridge, "stream_agent", mock_stream_agent)
    monkeypatch.setattr(service_mod, "stream_agent", mock_stream_agent)
    monkeypatch.delenv("OMNIAGENTS_LEGACY_QUERY_AUTH", raising=False)

    spec = AgentSpec(
        name="ConvAgent",
        welcome_text="Conv",
        get_agent_instructions=lambda: "Conv",
        build_agent=mock_build_agent,
        security_config={
            "providers": {
                "auth": {
                    "type": "token",
                    "tokens": {"alice-token": "alice", "bob-token": "bob"},
                }
            }
        },
    )
    app = server_app.build_app(spec=spec, default_workspace_root="/tmp")
    client = TestClient(app)
    yield SimpleNamespace(client=client, service=app.state.agent_service)


@pytest.fixture
def single_user_context(monkeypatch):
    """Same app with no auth provider: one anonymous principal, so the
    authorization policy is not enforced (the open-source local default)."""
    from omniagents.core.agents import service as service_mod

    monkeypatch.setattr(runner_bridge, "stream_agent", mock_stream_agent)
    monkeypatch.setattr(service_mod, "stream_agent", mock_stream_agent)
    spec = AgentSpec(
        name="ConvAgentLocal",
        welcome_text="Conv",
        get_agent_instructions=lambda: "Conv",
        build_agent=mock_build_agent,
    )
    app = server_app.build_app(spec=spec, default_workspace_root="/tmp")
    client = TestClient(app)
    yield SimpleNamespace(client=client, service=app.state.agent_service)


def connect(context, headers, experimental=()):
    cm = context.client.websocket_connect("/ws", headers=headers)
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


def test_a_completed_run_is_readable_as_domain_objects(context):
    cm, rpc = connect(context, ALICE)
    try:
        started = run_turn(rpc, "read the file")
        thread_id = started["session_id"]

        thread = rpc.result("get_thread", thread_id=thread_id)
        assert thread["thread_id"] == thread_id
        assert thread["source"] == "recorded"
        assert thread["turn_count"] == 1
        assert thread["status"] == "active"

        turns = rpc.result("list_turns", thread_id=thread_id)
        assert len(turns["turns"]) == 1
        turn = turns["turns"][0]
        assert turn["turn_id"] == started["run_id"]
        assert turn["ordinal"] == 1
        assert turn["status"] == "completed"
        assert turn["prompt"] == "read the file"

        page = rpc.result("list_items", thread_id=thread_id)
        kinds = [item["kind"] for item in page["items"]]
        assert kinds == ["user_message", "tool_call", "tool_call", "agent_message"]
        # Every item landed in exactly ONE authoritative terminal state.
        assert {item["status"] for item in page["items"]} == {"completed"}
        assert [item["seq"] for item in page["items"]] == [1, 2, 3, 4]

        tools = [item for item in page["items"] if item["kind"] == "tool_call"]
        assert tools[0]["content"]["tool_kind"] == "file"
        assert tools[0]["content"]["output"] == "def f(): ..."
        assert tools[1]["content"]["tool_kind"] == "mcp"
        assert tools[1]["content"]["server_label"] == "docs"
    finally:
        cm.__exit__(None, None, None)


def test_items_are_addressable_by_canonical_id(context):
    cm, rpc = connect(context, ALICE)
    try:
        started = run_turn(rpc, "hello")
        thread_id = started["session_id"]
        page = rpc.result("list_items", thread_id=thread_id)
        target = page["items"][1]
        fetched = rpc.result("get_item", thread_id=thread_id, item_id=target["item_id"])
        assert fetched == target
        assert target["item_id"].startswith("itm_")
    finally:
        cm.__exit__(None, None, None)


def test_multi_turn_threads_page_by_turn(context):
    cm, rpc = connect(context, ALICE)
    try:
        first = run_turn(rpc, "turn one")
        thread_id = first["session_id"]
        second = run_turn(rpc, "turn two", session_id=thread_id)

        turns = rpc.result("list_turns", thread_id=thread_id)
        assert [t["ordinal"] for t in turns["turns"]] == [1, 2]
        assert turns["total"] == 2

        scoped = rpc.result("list_items", thread_id=thread_id, turn_id=second["run_id"])
        assert scoped["turn_id"] == second["run_id"]
        assert {item["turn_id"] for item in scoped["items"]} == {second["run_id"]}
        assert [item["kind"] for item in scoped["items"]] == [
            "user_message",
            "tool_call",
            "tool_call",
            "agent_message",
        ]
    finally:
        cm.__exit__(None, None, None)


def test_pagination_walks_a_thread_with_a_cursor(context):
    cm, rpc = connect(context, ALICE)
    try:
        started = run_turn(rpc, "turn one")
        thread_id = started["session_id"]
        for index in range(4):
            run_turn(rpc, f"turn {index + 2}", session_id=thread_id)

        total = rpc.result("list_items", thread_id=thread_id, limit=1)["total"]
        assert total == 20  # 5 turns x (prompt + 2 tools + message)

        seen = []
        cursor = None
        while True:
            params = {"thread_id": thread_id, "limit": 3}
            if cursor:
                params["cursor"] = cursor
            page = rpc.result("list_items", **params)
            seen.extend(item["seq"] for item in page["items"])
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]
        assert seen == list(range(1, total + 1))
    finally:
        cm.__exit__(None, None, None)


def test_kind_filter_narrows_the_page(context):
    cm, rpc = connect(context, ALICE)
    try:
        started = run_turn(rpc, "hello")
        page = rpc.result(
            "list_items", thread_id=started["session_id"], kinds=["tool_call"]
        )
        assert {item["kind"] for item in page["items"]} == {"tool_call"}
    finally:
        cm.__exit__(None, None, None)


def test_typed_errors_use_the_reserved_codes(context):
    cm, rpc = connect(context, ALICE)
    try:
        started = run_turn(rpc, "hello")
        thread_id = started["session_id"]

        error = rpc.error("get_item", thread_id=thread_id, item_id="itm_nope")
        assert error["code"] == -32081
        assert error["data"]["kind"] == "item_not_found"

        error = rpc.error("list_items", thread_id=thread_id, cursor="!!!bad!!!")
        assert error["code"] == -32082
        assert error["data"]["kind"] == "cursor_invalid"
    finally:
        cm.__exit__(None, None, None)


def test_an_unknown_thread_is_denied_not_disclosed_in_multi_user_mode(context):
    """Ownership of a thread nobody owns cannot be established, and a failed
    ownership lookup denies (fail closed). The -32014 also means an attacker
    cannot probe which thread ids exist."""
    cm, rpc = connect(context, ALICE)
    try:
        error = rpc.error("get_thread", thread_id="does-not-exist")
        assert error["code"] == RPC_ERROR_AUTHORIZATION_DENIED
    finally:
        cm.__exit__(None, None, None)


def test_thread_not_found_surfaces_in_single_user_mode(single_user_context):
    """The local/open-source topology has one anonymous principal, so
    ownership rules are vacuous and the typed -32080 reaches the client."""
    cm, rpc = connect(single_user_context, {})
    try:
        error = rpc.error("get_thread", thread_id="does-not-exist")
        assert error["code"] == -32080
        assert error["data"] == {
            "kind": "thread_not_found",
            "thread_id": "does-not-exist",
        }

        error = rpc.error("list_turns", thread_id="does-not-exist")
        assert error["code"] == -32080
    finally:
        cm.__exit__(None, None, None)


def test_cross_user_reads_are_denied(context):
    """Thread/turn/item reads are session-owned data: another principal
    must not be able to read a transcript, page it, or address one item."""
    alice_cm, alice = connect(context, ALICE)
    bob_cm, bob = connect(context, BOB)
    try:
        started = run_turn(alice, "alice's private work")
        thread_id = started["session_id"]
        item_id = alice.result("list_items", thread_id=thread_id)["items"][0]["item_id"]

        for method, params in [
            ("get_thread", {"thread_id": thread_id}),
            ("list_turns", {"thread_id": thread_id}),
            ("list_items", {"thread_id": thread_id}),
            ("list_items", {"thread_id": thread_id, "turn_id": started["run_id"]}),
            ("get_item", {"thread_id": thread_id, "item_id": item_id}),
        ]:
            error = bob.error(method, **params)
            assert error["code"] == RPC_ERROR_AUTHORIZATION_DENIED, (method, error)
            assert error["data"]["kind"] == "authorization_denied"
            assert error["data"]["resource"] == "thread"

        # Alice still reads her own thread.
        assert alice.result("get_thread", thread_id=thread_id)["thread_id"] == thread_id
    finally:
        bob_cm.__exit__(None, None, None)
        alice_cm.__exit__(None, None, None)


LEGACY_ROWS = [
    {"role": "user", "content": "old question"},
    {
        "type": "function_call",
        "call_id": "old1",
        "name": "read_file",
        "arguments": "{}",
    },
    {"type": "function_call_output", "call_id": "old1", "output": "old contents"},
    {"role": "assistant", "type": "message", "content": "old answer"},
]


def test_resuming_a_pre_existing_session_keeps_its_transcript(context):
    """REGRESSION over the real socket.

    A session written by a previous release, then resumed on this branch.
    The recorder claims the thread on the first notification of the new run;
    when projection was a read-time fallback that claim locked the adapter
    out and the entire prior transcript vanished from the read surface.
    """
    from omniagents.core.session.history_db import append_message, register_session

    thread_id = "sess_resumed_legacy"
    register_session(thread_id, user_id="alice")
    for row in LEGACY_ROWS:
        append_message(thread_id, row)

    cm, rpc = connect(context, ALICE)
    try:
        run_turn(rpc, "new question", session_id=thread_id)
        page = rpc.result("list_items", thread_id=thread_id, limit=100)
        rendered = [
            item["content"].get("text") or item["content"].get("tool")
            for item in page["items"]
        ]
        assert "old question" in rendered
        assert "read_file" in rendered
        assert "old answer" in rendered
        assert "new question" in rendered
        seqs = [item["seq"] for item in page["items"]]
        assert seqs == sorted(set(seqs)) == seqs
    finally:
        cm.__exit__(None, None, None)


def test_a_second_turn_does_not_destroy_the_first(context):
    """REGRESSION: a read used to roll the ordinal cursor backwards, and the
    next write then overwrote the row holding the reused ordinal."""
    cm, rpc = connect(context, ALICE)
    try:
        first = run_turn(rpc, "turn one")
        thread_id = first["session_id"]
        rpc.result("list_items", thread_id=thread_id)
        rpc.result("get_thread", thread_id=thread_id)
        run_turn(rpc, "turn two", session_id=thread_id)

        page = rpc.result("list_items", thread_id=thread_id, limit=100)
        seqs = [item["seq"] for item in page["items"]]
        assert len(seqs) == len(set(seqs)), f"duplicate ordinals: {seqs}"
        prompts = [
            item["content"].get("text")
            for item in page["items"]
            if item["kind"] == "user_message"
        ]
        assert prompts == ["turn one", "turn two"]
        assert len(page["items"]) == 8
    finally:
        cm.__exit__(None, None, None)


def test_a_forked_session_reads_back_with_its_transcript(context):
    """REGRESSION: forks used to read back EMPTY."""
    cm, rpc = connect(context, ALICE, experimental=("fork_session",))
    try:
        started = run_turn(rpc, "original work")
        parent_id = started["session_id"]
        parent_items = rpc.result("list_items", thread_id=parent_id)["items"]

        forked = rpc.result("fork_session", session_id=parent_id)
        child_id = forked["new_session_id"]

        child = rpc.result("get_thread", thread_id=child_id)
        assert child["parent_thread_id"] == parent_id
        assert child["branched_from_item_id"] == parent_items[-1]["item_id"]
        assert child["item_count"] == len(parent_items)

        child_items = rpc.result("list_items", thread_id=child_id)["items"]
        assert [i["kind"] for i in child_items] == [i["kind"] for i in parent_items]
        assert [i["content"] for i in child_items] == [
            i["content"] for i in parent_items
        ]
        assert {i["item_id"] for i in child_items}.isdisjoint(
            {i["item_id"] for i in parent_items}
        )
    finally:
        cm.__exit__(None, None, None)


def test_deleting_a_session_removes_its_domain_rows(context):
    cm, rpc = connect(context, ALICE)
    try:
        started = run_turn(rpc, "sensitive work")
        thread_id = started["session_id"]
        assert rpc.result("list_items", thread_id=thread_id)["items"]
        assert rpc.result("delete_session", session_id=thread_id) is True
        error = rpc.error("get_thread", thread_id=thread_id)
        # The thread is gone; in enforced mode an unresolvable owner denies.
        assert error["code"] in (-32080, RPC_ERROR_AUTHORIZATION_DENIED)
    finally:
        cm.__exit__(None, None, None)


def test_archiving_a_session_marks_the_thread_archived(context):
    cm, rpc = connect(context, ALICE)
    try:
        started = run_turn(rpc, "work to shelve")
        thread_id = started["session_id"]
        assert rpc.result("archive_session", session_id=thread_id) is True
        assert rpc.result("get_thread", thread_id=thread_id)["status"] == "archived"
    finally:
        cm.__exit__(None, None, None)


def test_mid_run_user_messages_land_in_the_transcript(context):
    """Drives the real ``send_user_message`` RPC against a live run."""
    cm, rpc = connect(context, ALICE)
    try:
        started = run_turn(rpc, "first")
        thread_id = started["session_id"]
        # Re-point the run map at the (now finished) run so the RPC resolves
        # its session exactly as it does mid-run; the handler's history
        # append and domain recording are what is under test.
        service = context.service
        session = service._session_manager.get(thread_id)
        service._session_manager._task_map[started["run_id"]] = session

        rpc.result(
            "send_user_message", run_id=started["run_id"], content="one more thing"
        )

        texts = [
            item["content"].get("text")
            for item in rpc.result("list_items", thread_id=thread_id)["items"]
            if item["kind"] == "user_message"
        ]
        assert texts == ["first", "one more thing"]
    finally:
        cm.__exit__(None, None, None)
