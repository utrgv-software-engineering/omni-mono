"""Tests for the builtin feedback / artifact / model-picker server functions."""

import pytest

from omniagents.core.models import set_model_store


class _FakeStore:
    """Minimal ModelConfigStore stand-in for the picker server functions."""

    def __init__(self):
        self.voice_default = None
        self.single_provider = False

    def list_text_models(self):
        return [
            {
                "name": "gpt-test",
                "label": "GPT Test",
                "provider": "openai",
                "is_default": True,
                "is_user_defined": False,
            }
        ]

    def list_voice_models(self):
        return [
            {
                "name": "gpt-realtime",
                "label": "Realtime",
                "provider": "openai",
                "is_voice_default": True,
                "realtime": True,
            }
        ]

    def get_default_model_name(self):
        return "gpt-test"

    def get_default_model_config(self):
        return {"name": "gpt-test", "label": "GPT Test", "provider": "openai"}

    def normalize_model_ref(self, ref):
        return ref

    def get_model_config(self, ref):
        if ref == "gpt-test":
            return {"name": ref, "label": "Test", "provider": "openai"}
        if ref == "gpt-realtime":
            return {
                "name": ref,
                "label": "Realtime",
                "provider": "openai",
                "realtime": True,
            }
        return None

    def get_voice_default_model_name(self):
        return self.voice_default

    def set_voice_default_model(self, name):
        self.voice_default = name
        return True


@pytest.fixture
def fake_store(monkeypatch):
    # monkeypatch restores the process-wide store afterwards: a leaked fake
    # broke unrelated tests that ran later on the same xdist worker.
    import omniagents.core.models as models_pkg

    monkeypatch.setattr(models_pkg, "_ACTIVE_STORE", models_pkg._ACTIVE_STORE)
    store = _FakeStore()
    set_model_store(store)
    yield store


class _FakeSession:
    def __init__(self, sid="sess-1"):
        self.id = sid
        self.context = {}
        self.variables = {}


class _FakeManager:
    def __init__(self, session):
        self._session = session

    def get_or_create(self, _):
        return self._session


class _FakeService:
    def __init__(self, session=None):
        self._session = session or _FakeSession()
        self._session_manager = _FakeManager(self._session)
        self.server_calls = []
        self.client_calls = []

    async def server_call(self, function, args=None, session_id=None):
        self.server_calls.append((function, args, session_id))
        return {"session_id": session_id}

    async def request_client_function(self, function, args=None, **kwargs):
        self.client_calls.append((function, args, kwargs.get("session")))
        return True


# -- feedback ----------------------------------------------------------


@pytest.mark.asyncio
async def test_good_and_bad_accept_empty_text():
    from omniagents.builtin.server_functions.feedback import bad, good

    assert (await good(_FakeSession(), ""))["ok"] is True
    assert (await bad(_FakeSession(), ""))["ok"] is True


# -- artifact ----------------------------------------------------------


@pytest.mark.asyncio
async def test_artifact_emits_ui_add_artifact():
    from omniagents.builtin.server_functions import artifact as artifact_mod

    handler = getattr(artifact_mod.artifact, "_server_function_on_invoke")
    svc = _FakeService()
    result = await handler(
        svc,
        svc._session,
        {
            "title": "Report",
            "content": "# Hello",
            "mode": "markdown",
            "artifact_id": "rep_1",
        },
    )

    assert result == {"ok": True}
    assert svc.client_calls == [
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


# -- model picker ------------------------------------------------------


@pytest.mark.asyncio
async def test_models_lists_and_ensures_session(fake_store):
    from omniagents.builtin.server_functions import model_picker

    handler = getattr(model_picker.list_models, "_server_function_on_invoke")
    svc = _FakeService()
    result = await handler(svc, None, {})

    assert svc.server_calls == []
    assert result["current"] == "gpt-test"
    assert result["models"][0]["is_current"] is True


@pytest.mark.asyncio
async def test_switch_model_sets_session_state(fake_store):
    from omniagents.builtin.server_functions import model_picker

    handler = getattr(model_picker.switch_model, "_server_function_on_invoke")
    svc = _FakeService(_FakeSession("sess-2"))
    result = await handler(svc, None, {"text": "gpt-test"})

    assert svc.server_calls == []
    assert result["model"] == "gpt-test"
    assert svc._session.active_model == "gpt-test"
    # UI notified of the session-state change.
    assert any(c[0] == "ui.set_session_state" for c in svc.client_calls)


@pytest.mark.asyncio
async def test_switch_model_rejects_unknown(fake_store):
    from omniagents.builtin.server_functions import model_picker

    handler = getattr(model_picker.switch_model, "_server_function_on_invoke")
    svc = _FakeService()
    result = await handler(svc, None, {"text": "nope"})
    assert "error" in result
    assert svc.client_calls == []  # no UI notify on error


@pytest.mark.asyncio
async def test_reasoning_set_and_validate(fake_store):
    from omniagents.builtin.server_functions import model_picker

    handler = getattr(model_picker.set_reasoning, "_server_function_on_invoke")
    svc = _FakeService()
    result = await handler(svc, None, {"text": "high"})
    assert result["reasoning_effort"] == "high"
    assert svc._session.reasoning_effort == "high"

    result = await handler(svc, None, {"text": "bogus"})
    assert "error" in result


@pytest.mark.asyncio
async def test_voice_model_switch_sets_store_default(fake_store):
    from omniagents.builtin.server_functions import model_picker

    class _ChannelService(_FakeService):
        def __init__(self):
            super().__init__()

            class _Chan:
                async def async_call(self, *_a, **_k):
                    return None

            self.channel = _Chan()

    handler = getattr(model_picker.switch_voice_model, "_server_function_on_invoke")
    svc = _ChannelService()
    result = await handler(svc, None, {"text": "gpt-realtime"})
    assert result["model"] == "gpt-realtime"
    assert fake_store.voice_default == "gpt-realtime"

    # Text models are rejected as voice models.
    result = await handler(svc, None, {"text": "gpt-test"})
    assert "error" in result


@pytest.mark.asyncio
async def test_picker_without_store_reports_hint(monkeypatch):
    import omniagents.core.models as models_pkg
    from omniagents.builtin.server_functions import model_picker

    monkeypatch.setattr(models_pkg, "_ACTIVE_STORE", None)
    result = await model_picker.list_models(_FakeSession())
    assert result["models"] == []
    assert result["message"]
