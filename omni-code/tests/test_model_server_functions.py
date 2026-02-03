import pytest


@pytest.mark.asyncio
async def test_models_ensure_session_proxies_to_builtin(monkeypatch):
    from server_functions import model as model_server
    import omni_code.models as models

    monkeypatch.setattr(models, "list_text_models", lambda: [])
    monkeypatch.setattr(models, "get_default_model_name", lambda: "gpt-test")
    monkeypatch.setattr(models, "normalize_model_ref", lambda value: value)

    handler = getattr(model_server.list_models, "_server_function_on_invoke")
    captured = []

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

        async def server_call(self, function, args=None, session_id=None):
            captured.append((function, args, session_id))
            return {"session_id": session_id}

    result = await handler(FakeService(), None, {})

    assert captured[0] == ("session.ensure", None, "sess-1")
    assert result["current"] == "gpt-test"


@pytest.mark.asyncio
async def test_model_ensure_session_then_switch(monkeypatch):
    from server_functions import model as model_server
    import omni_code.models as models

    monkeypatch.setattr(models, "normalize_model_ref", lambda value: value)
    monkeypatch.setattr(
        models,
        "get_model_config",
        lambda ref: {"name": ref, "label": "Test", "provider": "openai"},
    )
    monkeypatch.setattr(models, "list_text_models", lambda: [{"name": "gpt-test"}])

    handler = getattr(model_server.switch_model, "_server_function_on_invoke")
    captured = []

    class FakeSession:
        def __init__(self):
            self.id = "sess-2"
            self.context = {}
            self.variables = {}

    class FakeManager:
        def __init__(self, session):
            self._session = session

        def get_or_create(self, _):
            return self._session

    class FakeService:
        def __init__(self):
            self._session = FakeSession()
            self._session_manager = FakeManager(self._session)

        async def server_call(self, function, args=None, session_id=None):
            captured.append((function, args, session_id))
            return {"session_id": session_id}

        async def request_client_function(self, *_, **__):
            return None

    svc = FakeService()
    result = await handler(svc, None, {"text": "gpt-test"})

    assert captured[0] == ("session.ensure", None, "sess-2")
    assert result["model"] == "gpt-test"
    assert svc._session.active_model == "gpt-test"
