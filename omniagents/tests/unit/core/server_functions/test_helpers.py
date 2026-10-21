import asyncio
from types import SimpleNamespace

import pytest

from omniagents.core.server_functions.helpers import (
    ensure_server_session,
    invoke_artifact_bridge,
    notify_session_state,
    request_client_function_safe,
)


class DummyService:
    def __init__(self, *, session=None, fail=False):
        self.calls = []
        self.fail = fail
        self._session_manager = SimpleNamespace(get_or_create=lambda _sid: session)

    async def request_client_function(
        self, function_name, payload, *, session=None, expect_result=False
    ):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append((function_name, payload, session, expect_result))


def test_ensure_server_session_uses_manager_when_missing():
    session = SimpleNamespace(id="session-1")
    service = DummyService(session=session)

    ensured = asyncio.run(ensure_server_session(service, None))

    assert ensured is session


def test_request_client_function_safe_returns_false_on_error():
    service = DummyService(fail=True)

    ok = asyncio.run(
        request_client_function_safe(
            service,
            "ui.demo",
            {"a": 1},
            session=None,
        )
    )

    assert ok is False


def test_notify_session_state_sends_payload():
    session = SimpleNamespace(
        id="session-1",
        active_model="demo-model",
        model_config={
            "label": "Demo",
            "provider": "openai",
            "max_input_tokens": 100,
            "max_output_tokens": 50,
        },
        reasoning_effort="high",
    )
    service = DummyService(session=session)

    ok = asyncio.run(notify_session_state(service, session))

    assert ok is True
    assert service.calls[0][0] == "ui.set_session_state"
    assert service.calls[0][1]["model"] == "demo-model"
    assert service.calls[0][1]["reasoning_effort"] == "high"


def test_invoke_artifact_bridge_uses_session_and_returns_ok():
    session = SimpleNamespace(id="session-1")
    service = DummyService(session=session)

    result = asyncio.run(
        invoke_artifact_bridge(
            service,
            None,
            {"title": "T", "content": "C", "mode": "markdown", "artifact_id": 1},
        )
    )

    assert result == {"ok": True}
    assert service.calls[0][0] == "ui.add_artifact"
    assert service.calls[0][1]["artifact_id"] == "1"


def test_invoke_artifact_bridge_raises_without_session():
    service = DummyService(session=None)

    with pytest.raises(ValueError):
        asyncio.run(
            invoke_artifact_bridge(service, None, {"title": "T", "content": "C"})
        )
