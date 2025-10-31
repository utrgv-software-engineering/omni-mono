import pytest

import flet as ft


@pytest.fixture(autouse=True)
def patch_flet_tokens(monkeypatch):
    class DummyControl:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            self.content = kwargs.get("content")
            controls = kwargs.get("controls")
            if controls is None and args and isinstance(args[0], list):
                controls = args[0]
            self.controls = controls or []
            self.value = kwargs.get("value")
            if self.value is None and args:
                self.value = args[0]
            self.text = kwargs.get("text")
            if self.text is None and args:
                self.text = args[0]
            self.visible = kwargs.get("visible", False)
            self.on_click = kwargs.get("on_click")

        def update(self):
            self.kwargs["updated"] = True

    for name in [
        "Container",
        "Row",
        "Column",
        "Text",
        "Icon",
        "View",
        "Control",
        "Page",
        "TextField",
        "IconButton",
        "FilledButton",
        "ListView",
        "ButtonStyle",
        "SafeArea",
        "CircleBorder",
        "BoxShadow",
        "Offset",
        "LinearGradient",
        "Markdown",
        "TextButton",
        "Tabs",
        "Tab",
        "TabsTheme",
        "TabsScrollButtonTheme",
        "FilePicker",
        "TextStyle",
    ]:
        monkeypatch.setattr(ft, name, DummyControl, raising=False)

    for name, value in {
        "ARROW_BACK": "arrow_back",
        "ANALYTICS": "analytics",
        "ARTICLE": "article",
        "CHECK": "check",
        "INFO_OUTLINE": "info_outline",
    }.items():
        monkeypatch.setattr(ft.Icons, name, value, raising=False)

    for name, value in {
        "VERIFIED_USER": "verified_user",
        "EXPAND_LESS": "expand_less",
        "EXPAND_MORE": "expand_more",
        "DOWNLOAD": "download",
    }.items():
        monkeypatch.setattr(ft.icons, name, value, raising=False)

    for name, value in {
        "BLACK87": "#1f1f1f",
        "BLACK54": "#2f2f2f",
        "BLUE": "#2196f3",
        "BLUE_GREY_400": "#78909c",
        "BLUE_400": "#42a5f5",
        "BLUE_50": "#e3f2fd",
        "BLUE_700": "#1976d2",
        "GREEN": "#00ff00",
        "RED": "#ff0000",
    }.items():
        monkeypatch.setattr(ft.Colors, name, value, raising=False)

    monkeypatch.setattr(
        ft.MarkdownExtensionSet, "GITHUB_FLAVORED", "github", raising=False
    )

    monkeypatch.setattr(ft.MarkdownCodeTheme, "GITHUB", "github", raising=False)
