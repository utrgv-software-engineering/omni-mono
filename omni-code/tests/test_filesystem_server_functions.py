import pytest

from server_functions import filesystem


@pytest.mark.asyncio
async def test_session_ensure_proxies_to_builtin():
    handler = getattr(filesystem.session_ensure, "_server_function_on_invoke")
    captured = {}

    class FakeSession:
        id = "sess-ensured"
        context = {}
        variables = {}

    class FakeService:
        def get_or_create_session(self, _=None):
            return FakeSession()

        async def server_call(self, function, args=None, session_id=None):
            captured["function"] = function
            captured["args"] = args or {}
            captured["session_id"] = session_id
            return {
                "session_id": "sess-built-in",
                "workspace_root": args.get("workspace_root"),
            }

    result = await handler(
        FakeService(),
        None,
        {"workspace_root": "/workspace"},
    )

    assert captured["function"] == "session.ensure"
    assert captured["args"] == {"workspace_root": "/workspace"}
    assert result == {
        "session_id": "sess-built-in",
        "workspace_root": "/workspace",
    }


@pytest.mark.asyncio
async def test_session_ensure_fallback_sets_metadata():
    handler = getattr(filesystem.session_ensure, "_server_function_on_invoke")

    class FakeSession:
        def __init__(self):
            self.id = "sess-local"
            self.context = {}
            self.variables = {}

    class FailingService:
        def __init__(self):
            self._session = FakeSession()

        def get_or_create_session(self, _=None):
            return self._session

        async def server_call(self, *_, **__):
            raise ValueError("missing built-in")

    svc = FailingService()
    result = await handler(
        svc,
        None,
        {"workspace_root": "/tmp/project"},
    )

    assert result["session_id"] == "sess-local"
    assert result["workspace_root"] == "/tmp/project"
    assert svc._session.context["workspace_root"] == "/tmp/project"
    assert svc._session.variables["workspace_root"] == "/tmp/project"
