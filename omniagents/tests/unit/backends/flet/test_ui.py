import pytest

import flet as ft

from omniagents.backends.flet.ui import (
    ArtifactDisplay,
    ChatMessage,
    CollapsibleCard,
    Message,
    TabHeader,
    ToolApprovalRequest,
    ToolCallDisplay,
    ToolInteractionDisplay,
    ToolOutputDisplay,
)


@pytest.fixture(autouse=True)
def suppress_control_update(monkeypatch):
    original = ft.Control.update
    monkeypatch.setattr(ft.Control, "update", lambda self: None)
    yield
    monkeypatch.setattr(ft.Control, "update", original)


def test_tool_output_display_renders_dict():
    display = ToolOutputDisplay({"a": 1})
    assert isinstance(display.content.content, ft.Column)


def test_tool_output_display_truncates_long_text():
    display = ToolOutputDisplay("x" * 1100)
    assert isinstance(display.content.content, ft.Text)
    assert display.content.content.value.endswith("... [output truncated]")


def test_tool_output_display_parses_json_string():
    display = ToolOutputDisplay('{"value": 3}')
    assert isinstance(display.content.content, ft.Column)


def test_tool_interaction_display_handles_output():
    control = ToolInteractionDisplay(
        "calculator",
        '{"n": 1}',
        icon_map={"calculator": ft.Icons.ANALYTICS},
    )
    assert not control.has_output()
    assert control.icon == ft.Icons.ANALYTICS

    control.set_output('{"result": 2}')
    assert control.has_output()
    assert control.icon == ft.Icons.CHECK
    assert isinstance(control.body_widget, ft.Column)

    expanded_before = control.expanded
    control._toggle(None)
    assert control.expanded != expanded_before


def test_tool_call_display_uses_icon_map():
    display = ToolCallDisplay(
        "search",
        '{"q": "docs"}',
        icon_map={"search": "custom_icon"},
        color="#abcdef",
    )
    assert isinstance(display.content, ft.Container)


def test_tool_approval_request_callbacks():
    calls = {"approve": 0, "always": 0, "reject": 0}

    def approve(_):
        calls["approve"] += 1

    def always(_):
        calls["always"] += 1

    def reject(_):
        calls["reject"] += 1

    request = ToolApprovalRequest("tool", "{}", approve, always, reject)
    buttons = request.content.content.controls[2].controls
    for button in buttons:
        button.on_click(None)
    assert calls == {"approve": 1, "always": 1, "reject": 1}


def test_collapsible_card_with_body_text():
    card = CollapsibleCard("#123", "ICON", "Title", body="info")
    assert card.expanded is False
    card._toggle(None)
    assert card.expanded is True


def test_artifact_display_markdown_update():
    display = ArtifactDisplay("Doc", "content")
    display.update_content("New Doc", "updated")
    assert display.title == "New Doc"
    assert isinstance(display.body_widget, ft.Markdown)


def test_artifact_display_image_mode():
    display = ArtifactDisplay("Image", "path.png", mode="image")
    assert isinstance(display.body_widget, ft.FilledButton)
    display.update_content("Image", "path.png", mode="image")
    assert isinstance(display.body_widget, ft.FilledButton)


def test_tab_header_indicator_updates_visibility():
    header = TabHeader("Logs")
    assert not header.indicator.visible
    header.show(True)
    assert header.indicator.visible
    header.show(False)
    assert not header.indicator.visible


def test_chat_message_variants():
    user = ChatMessage(Message("You", "hi", "user"))
    system = ChatMessage(Message("System", "note", "system"))
    assistant = ChatMessage(Message("Assistant", "hello", "assistant"))
    assert isinstance(user.content, ft.Row)
    assert isinstance(system.content, ft.Row)
    assert isinstance(assistant.content, ft.Row)
