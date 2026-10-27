"""Memory isolation, history, expiry, persistence, and the authenticated MCP surface."""

import json
import time

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from conftest import factory_env
from omniagents_workplace.memory.state import MemoryState, MemoryError
from omniagents_workplace.store import Store


def test_history_search_expiry_and_restart(tmp_path):
    store = Store(tmp_path / "workplace.db")
    state = MemoryState(store)
    own = state.provision("alice", "personal")
    fact = state.remember(
        "alice",
        own,
        "Deploy with the staging runner",
        tags=["deploy"],
        source={"run_id": "12"},
    )
    state.update("alice", fact["id"], "Deploy with the production runner")
    assert len(state.get("alice", fact["id"])["history"]) == 2
    assert state.search("alice", 'runner OR "bad syntax')[0]["text"].endswith(
        "production runner"
    )
    note = state.remember(
        "alice", own, "Pending pull request", kind="note", tags=["acme/api#5"]
    )
    state.expire_tag("acme/api#5")
    assert not state.search("alice", "Pending")
    assert len(state.get("alice", note["id"])["history"]) == 2
    state.remember("alice", own, "old note", kind="note", expires_at=time.time() - 1)
    replacement = state.supersede("alice", [fact["id"]], "Deploy through CI")
    assert state.get("alice", fact["id"])["superseded_by"] == replacement["id"]
    store.close()
    reopened = Store(tmp_path / "workplace.db")
    state = MemoryState(reopened)
    assert [m["id"] for m in state.search("alice", "Deploy")] == [replacement["id"]]
    state.forget("alice", replacement["id"], "Promoted in PR #6")
    assert not state.search("alice", "Deploy")
    assert (
        state.get("alice", replacement["id"])["history"][-1]["reason"]
        == "Promoted in PR #6"
    )
    reopened.close()


def test_access_and_atomic_supersession(tmp_path):
    store = Store(tmp_path / "workplace.db")
    state = MemoryState(store)
    alice = state.provision("alice", "personal")
    bob = state.provision("bob", "agent")
    private = state.remember("alice", alice, "Private preference", kind="core")
    other = state.remember("bob", bob, "Other fact")
    assert not state.search("bob", "Private")
    for call in (
        lambda: state.get("bob", private["id"]),
        lambda: state.search("bob", "Private", [alice]),
        lambda: state.update("bob", private["id"], "changed"),
        lambda: state.remember("bob", "team", "write forbidden"),
        lambda: state.supersede("bob", [other["id"], private["id"]], "replacement"),
    ):
        with pytest.raises(MemoryError):
            call()
    assert len(state.search("bob", "Other")) == 1
    role_store = bob + "/implementer"
    state.remember("bob", role_store, "Role-specific lesson")
    state.grant("bob", bob, "none")
    with pytest.raises(MemoryError):
        state.remember("bob", bob + "/new-role", "Parent access revoked")
    state.grant("bob", bob, "write")
    state.grant("bob", alice, "read")
    assert state.core("bob")[0]["id"] == private["id"]
    with pytest.raises(MemoryError):
        state.forget("bob", private["id"], "no")
    with pytest.raises(MemoryError):
        state.remember("alice", alice, "x" * 8001, kind="core")
    store.close()


@pytest.mark.asyncio
async def test_memory_mcp_access_and_revocation(workplace_server):
    workplace, base_url = workplace_server
    env = factory_env(base_url)
    token = env["MEMORY_MCP_TOKEN"]
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http:
        async with streamable_http_client(
            f"{base_url}/memory/mcp", http_client=http
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                assert init.serverInfo.name == "memory"
                result = await session.call_tool(
                    "remember",
                    {"store": env["MEMORY_AGENT_STORE"], "text": "Check pagination"},
                )
                assert not result.isError, result
                result = await session.call_tool("search", {"query": "pagination"})
                assert (
                    json.loads(result.content[0].text)[0]["text"] == "Check pagination"
                )
                refused = await session.call_tool(
                    "remember", {"store": "team", "text": "not allowed"}
                )
                assert refused.isError
    async with httpx.AsyncClient() as http:
        assert (await http.post(f"{base_url}/memory/mcp", json={})).status_code == 401


def test_personal_api_history_tokens_and_private_wire(workplace_server):
    from conftest import signed_in

    workplace, base_url = workplace_server
    with signed_in(workplace, base_url, "dana") as dana, signed_in(
        workplace, base_url, "priya"
    ) as priya:
        stores = dana.get("/workplace/api/memory/stores").json()
        own = next(s["id"] for s in stores if s["id"].startswith("personal:"))
        made = dana.post(
            "/workplace/api/memory/remember",
            json={"store": own, "text": "Private review preference"},
        ).json()
        assert "id" in made
        assert (
            priya.post("/workplace/api/memory/get", json={"id": made["id"]}).status_code
            == 403
        )
        edited = dana.post(
            "/workplace/api/memory/update",
            json={"id": made["id"], "text": "Updated preference"},
        )
        assert edited.status_code == 200
        history = dana.post(
            "/workplace/api/memory/get", json={"id": made["id"]}
        ).json()["history"]
        assert [m["text"] for m in history] == [
            "Private review preference",
            "Updated preference",
        ]
        token = dana.post("/workplace/api/me/tokens/memory").json()["token"]
        assert (
            workplace.memory_mcp._caller({"authorization": f"Bearer {token}"}).id
            == workplace.accounts.by_username("dana").id
        )
        dana.delete("/workplace/api/me/tokens/memory")
        assert (
            workplace.memory_mcp._caller({"authorization": f"Bearer {token}"}) is None
        )
        assert "Private review preference" not in json.dumps(
            [entry.to_dict() for entry in workplace.wire.recent()]
        )


@pytest.mark.asyncio
async def test_lesson_from_one_run_retrieved_on_next(workplace_server, monkeypatch):
    from omniagents.core.memory.client import MemoryClient
    from omniagents.core.memory.extract import extract

    workplace, base_url = workplace_server
    for key, value in factory_env(base_url).items():
        if key.startswith("MEMORY_"):
            monkeypatch.setenv(key, value)
    config = {
        "agent": "implementer",
        "stores": {"team": {"access": "read"}, "agent": {"access": "write"}},
    }
    client = MemoryClient(config)
    own = next(s for s in client.stores if s != "team")

    async def generate(prompt):
        return {
            "memories": [
                {
                    "store": own,
                    "text": "Pagination requires a stable cursor",
                    "kind": "fact",
                }
            ]
        }

    await extract(
        config,
        [{"role": "assistant", "content": "Pagination failed without a stable cursor"}],
        {"run_id": "1", "session_id": "first"},
        generate=generate,
    )
    next_input = await client.relevant("Fix pagination")
    assert "Pagination requires a stable cursor" in next_input
    assert '"run_id": "1"' in next_input
    assert "Pagination requires a stable cursor" not in json.dumps(
        [entry.to_dict() for entry in workplace.wire.recent()]
    )


@pytest.mark.asyncio
async def test_memory_webhooks_are_signed_and_scoped(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    import hashlib
    import hmac
    from omniagents_workplace.memory.webhooks import MemoryWebhooks

    store = Store(tmp_path / "workplace.db")
    state = MemoryState(store)
    own = state.provision("app", "agent")
    app = SimpleNamespace(
        id="app",
        removed=False,
        memory_webhook="http://example.test/events",
        memory_webhook_secret="secret",
    )
    workplace = SimpleNamespace(
        memory=state, apps=SimpleNamespace(active=lambda: [app], apps={"app": app})
    )
    hooks = MemoryWebhooks(workplace)
    hooks.loop = SimpleNamespace(call_soon_threadsafe=Mock())
    hooks.changed(
        "memory_created", {"memory": {"store": "personal:someone", "text": "private"}}
    )
    hooks.loop.call_soon_threadsafe.assert_not_called()
    hooks.changed("memory_created", {"memory": {"store": own, "text": "lesson"}})
    payload = hooks.loop.call_soon_threadsafe.call_args.args[2]
    sent = []
    original = httpx.AsyncClient

    def receive(request):
        assert (
            request.headers["Memory-Signature"]
            == hmac.new(b"secret", request.content, hashlib.sha256).hexdigest()
        )
        sent.append(json.loads(request.content))
        return httpx.Response(200)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(receive), **kwargs),
    )
    await hooks.deliver(app.id, payload)
    assert sent[0]["memory"]["text"] == "lesson"
    state.grant("app", own, "none")
    await hooks.deliver(app.id, payload)
    assert len(sent) == 1
    store.close()


@pytest.mark.asyncio
async def test_improver_retires_role_memory_with_history_and_backend_grants(
    workplace_server, monkeypatch
):
    from omniagents.core.memory.client import MemoryClient

    workplace, base_url = workplace_server
    env = factory_env(base_url)
    for key, value in env.items():
        if key.startswith("MEMORY_"):
            monkeypatch.setenv(key, value)
    implementer = MemoryClient(
        {"agent": "implementer", "stores": {"agent": {"access": "write"}}}
    )
    store = next(iter(implementer.stores))
    lesson = await implementer.tool(
        "remember", {"store": "agent", "text": "Use a stable pagination cursor"}
    )
    improver = MemoryClient(
        {"agent": "improver", "stores": {store: {"access": "read", "forget": True}}}
    )
    reason = "Promoted in merged PR https://workplace/acme/factory/pull/42"
    await improver.tool("forget", {"id": lesson["id"], "reason": reason})
    assert (
        await implementer.call("search", {"query": "pagination", "stores": [store]})
        == []
    )
    retired = await improver.tool("get", {"id": lesson["id"]})
    assert retired["forgotten"] is True
    assert retired["history"][-1]["reason"] == reason
    assert retired["history"][0]["text"] == lesson["text"]

    other = await implementer.tool(
        "remember", {"store": "agent", "text": "Another lesson"}
    )
    app = workplace.apps.by_username("acme-factory")
    workplace.memory.grant(app.id, store, "read")
    with pytest.raises(ValueError, match="No access"):
        await improver.tool("forget", {"id": other["id"], "reason": reason})
    assert not (await improver.tool("get", {"id": other["id"]})).get("forgotten")
