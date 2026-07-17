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
async def test_session_ensure_uses_existing_session_id():
    handler = getattr(filesystem.session_ensure, "_server_function_on_invoke")
    captured = {}

    class FakeSession:
        id = "sess-existing"
        context = {}
        variables = {}

    class FakeService:
        async def server_call(self, function, args=None, session_id=None):
            captured["session_id"] = session_id
            return {"session_id": session_id}

    result = await handler(FakeService(), FakeSession(), None)

    assert captured["session_id"] == "sess-existing"
    assert result == {"session_id": "sess-existing"}
