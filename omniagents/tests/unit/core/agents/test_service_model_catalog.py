"""Tests for the AgentService model-catalog RPC methods
(``list_models`` / ``get_model`` / ``list_providers`` /
``set_session_model`` / ``set_session_reasoning`` / ``set_voice_model``)
and for catalog identities (``model_ref``) on usage events.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from omniagents.execution import AgentHostResources
from omniagents.core import models as models_pkg
from omniagents.core.agents import service as service_module
from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.models.store import ModelConfigStore


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


def make_spec():
    return AgentSpec(
        name="Agent",
        welcome_text="Welcome",
        get_agent_instructions=lambda: "",
        build_agent=default_build_agent,
    )


PROVIDERS = {
    "openai": {
        "type": "openai",
        "api_key": "sk-test",
        "models": {
            "gpt-5.1": {
                "model": "gpt-5.1",
                "label": "GPT-5.1",
                "reasoning": "medium",
            },
            "gpt-secret": {"model": "gpt-secret", "hidden": True},
            "gpt-realtime": {"model": "gpt-realtime", "realtime": True},
        },
    },
}


@pytest.fixture
def store(tmp_path):
    store = ModelConfigStore(
        config_dir=lambda: tmp_path,
        default_providers=lambda: json.loads(json.dumps(PROVIDERS)),
        default_model_fallback=lambda: "gpt-5.1",
    )
    models_pkg.set_model_store(store)
    yield store
    models_pkg.reset_model_store()


@pytest.fixture
def service():
    service = AgentService(
        make_spec(),
        AgentHostResources.create("test-unit-core-agents-service_model_catalog-host"),
    )
    service.channel = object()
    return service


def attach_session_state_capture(service):
    calls = []

    async def capture(function_name, payload, *, session=None, expect_result=False):
        calls.append({"function": function_name, "payload": payload})

    service.request_client_function = capture  # type: ignore[assignment]
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,field,value",
    [
        ("set_session_approvals", "approvals_reviewer", "auto"),
        ("set_session_workflow", "workflow_reviewer", "off"),
    ],
)
async def test_reviewer_changes_notify_other_session_clients(
    service, method, field, value
):
    calls = attach_session_state_capture(service)
    result = await getattr(service, method)("shared-session", value)
    assert result["ok"] is True
    assert len(calls) == 1
    assert calls[0]["function"] == "ui.set_session_state"
    assert calls[0]["payload"][field] == value
    refused = await getattr(service, method)("shared-session", "invalid")
    assert refused["ok"] is False
    assert len(calls) == 1
    session = service._session_manager.get_or_create("shared-session")
    assert getattr(session, field) == value


@pytest.mark.asyncio
async def test_list_models_returns_catalog_with_session_context(service, store):
    result = await service.list_models(session_id="sess-1")
    ids = [m["id"] for m in result["models"]]
    assert ids == ["gpt-5.1", "gpt-realtime"]  # hidden excluded, deterministic
    assert result["default_model"] == "gpt-5.1"
    assert result["reasons"] == []
    assert result["session"] == {
        "session_id": "sess-1",
        "active_model": None,
        "reasoning_effort": None,
        "approvals_reviewer": None,
        "workflow_reviewer": None,
    }

    hidden = await service.list_models(include_hidden=True)
    assert "gpt-secret" in [m["id"] for m in hidden["models"]]
    assert "session" not in hidden


@pytest.mark.asyncio
async def test_get_model_and_list_providers(service, store):
    found = await service.get_model(model="gpt-5.1")
    assert found["found"] is True
    assert found["model"]["label"] == "GPT-5.1"

    missing = await service.get_model(model="ghost")
    assert missing["found"] is False
    assert [r["code"] for r in missing["reasons"]] == ["unknown_model"]

    providers = await service.list_providers()
    (openai,) = providers["providers"]
    assert openai["name"] == "openai"
    assert openai["model_count"] == 2
    assert openai["hidden_model_count"] == 1
    assert openai["health"]["status"] == "ok"


@pytest.mark.asyncio
async def test_catalog_methods_without_store_return_typed_reason(service):
    models_pkg.reset_model_store()
    for result in (
        await service.list_models(),
        await service.list_providers(),
        await service.get_model(model="x"),
        await service.set_session_model(session_id="s", model="x"),
        await service.set_voice_model(model="x"),
    ):
        codes = [r["code"] for r in result["reasons"]]
        assert codes == ["no_model_store"]


@pytest.mark.asyncio
async def test_set_session_model_switches_and_notifies(service, store):
    calls = attach_session_state_capture(service)
    result = await service.set_session_model(session_id="sess-1", model="gpt-5.1")
    assert result["ok"] is True
    assert result["model"] == "gpt-5.1"
    assert result["session_id"] == "sess-1"

    sess = service._session_manager.get_or_create("sess-1")
    assert sess.active_model == "gpt-5.1"
    assert sess.reasoning_effort == "medium"

    (call,) = calls
    assert call["function"] == "ui.set_session_state"
    assert call["payload"]["model"] == "gpt-5.1"

    # The catalog now reports this session's selection.
    listed = await service.list_models(session_id="sess-1")
    assert listed["session"]["active_model"] == "gpt-5.1"


@pytest.mark.asyncio
async def test_set_session_model_refusals_are_typed_and_silent(service, store):
    calls = attach_session_state_capture(service)
    unknown = await service.set_session_model(session_id="s", model="ghost")
    assert unknown["ok"] is False
    assert [r["code"] for r in unknown["reasons"]] == ["unknown_model"]
    realtime = await service.set_session_model(session_id="s", model="gpt-realtime")
    assert [r["code"] for r in realtime["reasons"]] == ["realtime_only"]
    assert calls == []  # no session-state notification on refusal


@pytest.mark.asyncio
async def test_set_session_reasoning(service, store):
    calls = attach_session_state_capture(service)
    result = await service.set_session_reasoning(session_id="sess-1", effort="high")
    assert result["ok"] is True
    sess = service._session_manager.get_or_create("sess-1")
    assert sess.reasoning_effort == "high"
    assert calls and calls[0]["payload"]["reasoning_effort"] == "high"

    bad = await service.set_session_reasoning(session_id="sess-1", effort="ultra")
    assert bad["ok"] is False
    assert [r["code"] for r in bad["reasons"]] == ["invalid_reasoning_effort"]


@pytest.mark.asyncio
async def test_set_voice_model(service, store):
    result = await service.set_voice_model(model="gpt-realtime")
    assert result["ok"] is True
    assert store.get_voice_default_model_name() == "gpt-realtime"

    refused = await service.set_voice_model(model="gpt-5.1")
    assert refused["ok"] is False
    assert [r["code"] for r in refused["reasons"]] == ["not_realtime"]


# ---------------------------------------------------------------------------
# Usage events carry the catalog identity (model_ref)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_end_references_catalog_identity(monkeypatch, service, store):
    async def stream(*args, **kwargs):
        return {"usage": {"input_tokens": 1}, "model": "gpt-5.1-2026-01-01"}

    monkeypatch.setattr(service_module, "stream_agent", stream)
    attach_session_state_capture(service)

    events = []

    async def capture_broadcast(
        session, method, payload, *, queue_if_unseen=False, track_live=False
    ):
        events.append({"method": method, "payload": payload})
        return True

    service._broadcast_session = capture_broadcast  # type: ignore[assignment]

    await service.set_session_model(session_id="sess-run", model="gpt-5.1")
    await service.start_run("hello", {"mode": "none"}, session_id="sess-run")
    sess = service._session_manager.get_or_create("sess-run")
    if sess.current_task is not None:
        await sess.current_task

    run_end = next(e for e in events if e["method"] == "run_end")
    # The provider-reported model id stays in ``model``; the catalog
    # identity rides alongside so usage joins the catalog.
    assert run_end["payload"]["model"] == "gpt-5.1-2026-01-01"
    assert run_end["payload"]["model_ref"] == "gpt-5.1"
