import pytest

from omniagents.backends.flet import storage
from omniagents.core.agents.specs import SettingField


class DummyPage:
    def __init__(self, results=None, error=None):
        self.results = results or {}
        self.error = error
        self.calls = []

    async def _invoke_method_async(
        self, method_name, arguments, wait_timeout, wait_for_result=True
    ):
        self.calls.append((method_name, arguments, wait_timeout, wait_for_result))
        if self.error:
            raise self.error
        return self.results.get(arguments["key"], "")


@pytest.mark.asyncio
async def test_get_with_longer_timeout_returns_parsed_value():
    page = DummyPage({"item": '"stored"'})
    value = await storage.get_with_longer_timeout(page, "item", "default")
    assert value == "stored"


@pytest.mark.asyncio
async def test_get_with_longer_timeout_returns_default_on_missing():
    page = DummyPage()
    value = await storage.get_with_longer_timeout(page, "missing", "fallback")
    assert value == "fallback"


@pytest.mark.asyncio
async def test_get_with_longer_timeout_handles_exception():
    page = DummyPage(error=RuntimeError("boom"))
    value = await storage.get_with_longer_timeout(page, "key", "safe")
    assert value == "safe"


@pytest.mark.asyncio
async def test_load_settings_prefers_client_storage_and_env(monkeypatch):
    fields = [
        SettingField(key="token", label="Token", default="default-token"),
        SettingField(key="other", label="Other", default="default-other"),
    ]
    page = DummyPage(
        {
            "pref.token": '"client-token"',
            "pref.other": "",
        }
    )
    monkeypatch.setenv("OTHER", "env-value")
    loaded = await storage.load_settings(page, "pref.", fields)
    assert loaded["token"] == "client-token"
    assert loaded["other"] == "env-value"


@pytest.mark.asyncio
async def test_load_settings_falls_back_to_defaults_on_error(monkeypatch):
    async def boom(*_args, **_kwargs):
        raise RuntimeError("failure")

    monkeypatch.setattr(storage, "get_with_longer_timeout", boom)
    fields = [SettingField(key="token", label="Token", default="default")]
    page = DummyPage()
    loaded = await storage.load_settings(page, "pref.", fields)
    assert loaded == {"token": "default"}


@pytest.mark.asyncio
async def test_save_settings_uses_client_storage():
    page = DummyPage()
    await storage.save_settings(page, "pref.", {"token": "value", "count": 3})
    keys = [call[1]["key"] for call in page.calls]
    assert keys == ["pref.token", "pref.count"]
    values = [call[1]["value"] for call in page.calls]
    assert values == ["value", 3]
