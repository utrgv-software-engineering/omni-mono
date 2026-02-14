import pytest


@pytest.mark.asyncio
async def test_artifact_server_function_emits_ui_add_artifact():
    from server_functions import artifact as artifact_server

    handler = getattr(artifact_server.artifact, "_server_function_on_invoke")
    calls = []

    class FakeSession:
        def __init__(self):
            self.id = "sess-1"

    class FakeManager:
        def __init__(self, session):
            self._session = session

        def get_or_create(self, _):
            return self._session

    class FakeService:
        def __init__(self):
            self._session = FakeSession()
            self._session_manager = FakeManager(self._session)

        async def request_client_function(self, function, args=None, **kwargs):
            calls.append((function, args, kwargs.get("session")))
            return True

    svc = FakeService()
    result = await handler(
        svc,
        svc._session,
        {"title": "Report", "content": "# Hello", "mode": "markdown", "artifact_id": "rep_1"},
    )

    assert result == {"ok": True}
    assert calls == [
        (
            "ui.add_artifact",
            {
                "title": "Report",
                "content": "# Hello",
                "mode": "markdown",
                "artifact_id": "rep_1",
            },
            svc._session,
        )
    ]
