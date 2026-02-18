import pytest


def test_sandbox_cli_patches_websockets_max_size(monkeypatch):
    from omni_code import sandbox_cli

    websockets = pytest.importorskip("websockets")

    captured = {}

    def fake_connect(url, *args, **kwargs):
        captured["url"] = url
        captured["kwargs"] = dict(kwargs)
        return "ok"

    monkeypatch.setattr(websockets, "connect", fake_connect)
    monkeypatch.delattr(websockets, "_omni_code_unlimited", raising=False)

    sandbox_cli._ensure_websockets_unlimited()
    result = websockets.connect("ws://example")

    assert result == "ok"
    assert captured["kwargs"].get("max_size") is None
