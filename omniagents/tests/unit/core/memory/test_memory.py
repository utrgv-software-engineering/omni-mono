import json

import pytest

from omniagents.core.memory.client import MemoryClient
from omniagents.core.memory.extract import extract


@pytest.mark.asyncio
async def test_core_stability_retrieval_and_declared_permissions(monkeypatch):
    monkeypatch.setenv("MEMORY_AGENT_STORE", "agent:factory")
    client = MemoryClient(
        {
            "agent": "implementer",
            "stores": {"team": {"access": "read"}, "agent": {"access": "write"}},
        }
    )
    calls = []
    memory = {"id": "m1", "store": "team", "text": "Run staging tests", "updated_at": 1}

    async def call(name, args):
        calls.append((name, args))
        return memory if name == "get" else [memory]

    monkeypatch.setattr(client, "call", call)
    first = await client.core()
    memory["updated_at"] = 2
    assert await client.core() == first
    assert "Run staging tests" in await client.relevant("staging")
    assert calls[-1][1]["stores"] == ["team", "agent:factory/implementer"]
    with pytest.raises(ValueError, match="write access"):
        await client.tool("forget", {"id": "m1", "reason": "No access"})
    with pytest.raises(ValueError, match="not declared"):
        await client.tool(
            "search", {"query": "secret", "stores": ["personal:someone-else"]}
        )
    await client.tool("remember", {"store": "agent", "text": "Lesson"})
    assert calls[-1][1]["store"] == "agent:factory/implementer"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trigger,kind",
    [("run_end", "fact"), ("idle", "note"), ("compaction", "fact"), ("daily", "log")],
)
async def test_extraction_provenance_and_triggers(monkeypatch, trigger, kind):
    writes = []
    config = {
        "stores": {"agent:test": {"access": "write"}},
        "extraction": {"model": "default"},
    }

    async def call(self, name, args):
        if name in ("search", "core"):
            return []
        writes.append((name, args))
        return {"id": "new"}

    async def generate(prompt):
        data = json.loads(prompt)
        assert data["source"]["error"] == "tests failed"
        assert data["history"][0]["content"] == "Failure evidence"
        return {
            "memories": [
                {"store": "agent:test", "text": "Run migrations first", "kind": kind}
            ]
        }

    monkeypatch.setattr(MemoryClient, "call", call)
    assert (
        await extract(
            config,
            [{"role": "assistant", "content": "Failure evidence"}],
            {"run_id": "42", "error": "tests failed"},
            trigger,
            generate,
        )
        == 1
    )
    assert writes[0][1]["source"] == {
        "run_id": "42",
        "error": "tests failed",
        "trigger": trigger,
    }
    if kind == "log":
        assert writes[0][1]["date"]


@pytest.mark.asyncio
async def test_extractor_cannot_write_undeclared_store(monkeypatch):
    async def call(self, name, args):
        assert name == "search"
        return []

    async def generate(prompt):
        return {"memories": [{"store": "team", "text": "Attempted write"}]}

    monkeypatch.setattr(MemoryClient, "call", call)
    with pytest.raises(ValueError, match="write access"):
        await extract(
            {"stores": {"agent:test": {"access": "write"}, "team": {"access": "read"}}},
            ["history"],
            {},
            generate=generate,
        )


@pytest.mark.asyncio
async def test_memory_webhook_verifies_signature_before_intake(monkeypatch):
    import hashlib
    import hmac
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import httpx
    from fastapi import FastAPI
    from omniagents.integrations.memory import mount_memory_route

    intake = SimpleNamespace(accept=AsyncMock(return_value=True))
    app = FastAPI()
    mount_memory_route(app, intake)
    monkeypatch.setenv("MEMORY_WEBHOOK_SECRET", "test-secret")
    payload = json.dumps(
        {"id": "delivery-1", "event": "memory_created", "memory": {"id": "m1"}}
    ).encode()
    signature = hmac.new(b"test-secret", payload, hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        denied = await client.post("/integrations/memory/events", content=payload)
        assert denied.status_code == 401
        intake.accept.assert_not_awaited()
        accepted = await client.post(
            "/integrations/memory/events",
            content=payload,
            headers={"Memory-Signature": signature},
        )
        assert accepted.status_code == 200
    event = intake.accept.call_args.args[0]
    assert (event.provider, event.event, event.delivery_id) == (
        "memory",
        "memory_created",
        "delivery-1",
    )


@pytest.mark.asyncio
async def test_checkpoint_schedules_dropped_context_extraction(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from omniagents.core.runtime.compaction import run_checkpoint_compaction

    history = [{"role": "user", "content": "Remember this lesson"}]
    session = SimpleNamespace(
        id="personal-1",
        history=history,
        append_message=history.append,
        _memory_config={"extraction": {"triggers": ["compaction"]}},
    )
    schedule = Mock()
    monkeypatch.setattr("omniagents.core.memory.extract.schedule", schedule)
    monkeypatch.setattr(
        "omniagents.core.runtime.compaction._conversation_recorder", lambda _: None
    )

    async def summarize(prompt):
        return "A summary"

    await run_checkpoint_compaction(session, summarize)
    assert schedule.call_args.args[1] == [
        {"role": "user", "content": "Remember this lesson"}
    ]
    assert schedule.call_args.args[3] == "compaction"


@pytest.mark.asyncio
async def test_builder_appends_core_after_jinja_rendering(monkeypatch):
    from types import SimpleNamespace
    from omniagents.core.agents.builder import _default_build_agent
    from omniagents.core.agents.specs import AgentSpec

    async def call(self, name, args):
        assert name == "core"
        return [
            {"id": "m1", "store": "team", "text": "Keep {{ literal_braces }} intact"}
        ]

    monkeypatch.setattr(MemoryClient, "call", call)
    spec = AgentSpec(
        name="memory-test",
        welcome_text="",
        build_agent=None,
        get_agent_instructions=lambda: "Hello {{ context['user'] }}",
        memory_config={"stores": {"team": {"access": "read"}}},
    )
    agent = await _default_build_agent({}, None, spec, None)
    text = await agent.instructions(SimpleNamespace(context={"user": "Sam"}), agent)
    assert text.startswith("Hello Sam\n\n## Core memory")
    assert "Keep {{ literal_braces }} intact" in text
    assert any(t.name == "memory" for t in agent.tools)


@pytest.mark.asyncio
async def test_retirement_permission_does_not_allow_other_writes(monkeypatch):
    client = MemoryClient(
        {"stores": {"agent:factory/implementer": {"access": "read", "forget": True}}}
    )
    calls = []

    async def call(name, args):
        calls.append((name, args))
        return {"id": args.get("id"), "store": "agent:factory/implementer"}

    monkeypatch.setattr(client, "call", call)
    await client.tool("forget", {"id": "lesson", "reason": "Merged PR #42"})
    assert calls[-1] == ("forget", {"id": "lesson", "reason": "Merged PR #42"})
    for operation, args in (
        ("remember", {"store": "agent:factory/implementer", "text": "new"}),
        ("update", {"id": "lesson", "text": "changed"}),
        ("supersede", {"old_ids": ["lesson"], "text": "replacement"}),
    ):
        with pytest.raises(ValueError, match="write access"):
            await client.tool(operation, args)
    assert all(name in ("get", "forget") for name, _ in calls)


@pytest.mark.parametrize("permission", ["true", 1, None])
def test_retirement_permission_requires_boolean(permission):
    with pytest.raises(ValueError, match="must be a boolean"):
        MemoryClient({"stores": {"team": {"access": "read", "forget": permission}}})
