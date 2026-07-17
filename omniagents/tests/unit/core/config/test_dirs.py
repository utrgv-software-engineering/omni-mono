from pathlib import Path

from omniagents.core.config import product_config_dir


def test_xdg_override(monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "/custom/xdg")
    assert product_config_dir("myproduct") == Path("/custom/xdg/myproduct")


def test_default_home(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert product_config_dir("myproduct") == Path.home() / ".config" / "myproduct"


def test_windows_name_ignored_off_windows(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    got = product_config_dir("my_product", windows_name="MyProduct")
    assert got.name == "my_product"
