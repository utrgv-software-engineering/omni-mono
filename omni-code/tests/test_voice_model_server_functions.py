import pytest


@pytest.mark.asyncio
async def test_voice_models_ensure_session(monkeypatch):
    from server_functions import voice_model as voice_model_server
    import omni_code.models as models

    monkeypatch.setattr(models, "list_voice_models", lambda: [])
    monkeypatch.setattr(models, "get_voice_default_model_name", lambda: None)

    handler = getattr(voice_model_server.list_voice_models, "_server_function_on_invoke")
    captured = []

    class FakeSession:
        def __init__(self):
            self.id = "sess-1"

    class FakeService:
        def __init__(self):
            self._session = FakeSession()

        def get_or_create_session(self, _=None):
            return self._session

        async def server_call(self, function, args=None, session_id=None):
            captured.append((function, args, session_id))
            return {"session_id": session_id}

    result = await handler(FakeService(), None, {})

    assert captured[0] == ("session.ensure", None, "sess-1")
    assert result["current"] is None


@pytest.mark.asyncio
async def test_voice_model_switch_sets_voice_default(monkeypatch):
    from server_functions import voice_model as voice_model_server
    import omni_code.models as models

    monkeypatch.setattr(models, "normalize_model_ref", lambda value: value)
    monkeypatch.setattr(
        models,
        "get_model_config",
        lambda ref: {"name": ref, "label": "Voice", "provider": "openai", "realtime": True},
    )
    monkeypatch.setattr(models, "set_voice_default_model", lambda name: True)

    handler = getattr(voice_model_server.switch_voice_model, "_server_function_on_invoke")

    class FakeSession:
        def __init__(self):
            self.id = "sess-2"
            self.context = {}
            self.variables = {}

    class FakeChannel:
        async def async_call(self, *_, **__):
            return None

    class FakeService:
        def __init__(self):
            self._session = FakeSession()
            self.channel = FakeChannel()

        def get_or_create_session(self, _=None):
            return self._session

        async def server_call(self, function, args=None, session_id=None):
            return {"session_id": session_id}

    result = await handler(FakeService(), None, {"text": "gpt-realtime"})

    assert result["model"] == "gpt-realtime"
    assert result["provider"] == "openai"
