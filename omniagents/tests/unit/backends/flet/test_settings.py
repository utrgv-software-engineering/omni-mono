import pytest

import flet as ft

from omniagents.backends.flet.settings import SettingsScreen
from omniagents.core.agents.specs import AgentSpec, SettingField


async def _builder(settings, mcp, app):
    return None


def _make_spec():
    return AgentSpec(
        name="Spec",
        header_title="Spec",
        page_title="Spec",
        welcome_text="hi",
        client_storage_prefix="spec.",
        get_agent_instructions=lambda: "hi",
        build_agent=_builder,
    )


@pytest.mark.asyncio
async def test_settings_screen_collects_values(monkeypatch):
    monkeypatch.setattr(
        "agents.models._openai_shared.get_default_openai_client",
        lambda: None,
    )
    spec = _make_spec()
    fields = [
        SettingField(key="openai_api_key", label="API Key", default="", secret=True),
        SettingField(key="region", label="Region", default="us"),
    ]
    initial = {"openai_api_key": "abc", "region": "eu"}
    saved = {}

    async def handle_save(values):
        saved.update(values)

    backs = []

    screen = SettingsScreen(spec, fields, initial, handle_save, backs.append)
    assert set(screen.text_fields.keys()) == {"openai_api_key", "region"}
    assert screen.text_fields["openai_api_key"].value == "abc"
    assert screen.text_fields["region"].value == "eu"

    await screen._save_settings(None)
    assert saved == {"openai_api_key": "abc", "region": "eu"}

    screen._go_back(None)
    assert backs == [None]


def _get_list_content(screen):
    list_view = screen.controls[1]
    return getattr(list_view, "controls", [])


def _flatten_texts(controls):
    texts = []
    for control in controls:
        if isinstance(control, ft.Text):
            if control.value is not None:
                texts.append(control.value)
            elif getattr(control, "text", None) is not None:
                texts.append(control.text)
        content = getattr(control, "content", None)
        if isinstance(content, ft.Text):
            texts.append(content.value)
        elif hasattr(content, "controls"):
            texts.extend(_flatten_texts(content.controls))
        if hasattr(control, "controls"):
            texts.extend(_flatten_texts(control.controls))
    return texts


def _has_bgcolor(controls, color):
    for control in controls:
        if getattr(control, "kwargs", {}).get("bgcolor") == color:
            return True
        content = getattr(control, "content", None)
        if hasattr(content, "controls") and _has_bgcolor(content.controls, color):
            return True
        if hasattr(control, "controls") and _has_bgcolor(control.controls, color):
            return True
    return False


def test_settings_screen_hides_openai_fields_with_custom_client(monkeypatch):
    monkeypatch.setattr(
        "agents.models._openai_shared.get_default_openai_client",
        lambda: object(),
    )
    spec = _make_spec()
    fields = [
        SettingField(key="openai_api_key", label="API Key"),
        SettingField(key="custom", label="Custom"),
    ]
    initial = {"openai_api_key": "abc", "custom": "value"}

    async def noop(values):
        return None

    screen = SettingsScreen(spec, fields, initial, noop, lambda _: None)
    assert set(screen.text_fields.keys()) == {"custom"}

    assert _has_bgcolor(_get_list_content(screen), ft.Colors.BLUE_50)
