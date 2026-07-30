import asyncio
from typing import Any

from omni_code.agents.omni.runtime_hooks.token import on_token


class FakeService:
    def __init__(self):
        self.calls = []

    async def server_call(
        self, function: str, args=None, session_id: str | None = None
    ):
        self.calls.append(
            {"function": function, "args": args, "session_id": session_id}
        )


class FakeSession:
    def __init__(self, session_id: str):
        self.id = session_id
        self.variables: dict[str, Any] = {}


def test_token_hook_uses_total_tokens_for_utilization():
    service = FakeService()
    session = FakeSession("sess-1")

    params = {
        "max_input_tokens": 100,
        "delta": {"input_tokens": 95, "total_tokens": 1},
    }

    asyncio.run(
        on_token(
            service=service,
            session=session,
            run_id="run-1",
            params=params,
            event="token",
        )
    )

    assert service.calls == []
    assert "omni_code" not in session.variables


def test_token_hook_triggers_compact_when_total_tokens_over_threshold():
    service = FakeService()
    session = FakeSession("sess-2")

    params = {
        "max_input_tokens": 100,
        "delta": {"input_tokens": 1, "total_tokens": 95},
    }

    asyncio.run(
        on_token(
            service=service,
            session=session,
            run_id="run-2",
            params=params,
            event="token",
        )
    )

    assert service.calls == [
        {"function": "compact", "args": {}, "session_id": "sess-2"}
    ]
    assert session.variables["omni_code"]["needs_context_compression"] is True
    assert abs(session.variables["omni_code"]["context_utilization"] - 0.95) < 1e-9
    assert session.variables["omni_code"]["context_utilization_run_id"] == "run-2"
