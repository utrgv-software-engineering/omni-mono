import os
from pathlib import Path

import pytest

from omniagents.core.config import product_config_dir

posix_only = pytest.mark.skipif(
    os.name == "nt", reason="XDG layout is not used on Windows"
)
windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows config layout")


def test_xdg_override(monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "/custom/xdg")
    monkeypatch.setenv("APPDATA", "/custom/xdg")  # Windows config root
    assert product_config_dir("myproduct") == Path("/custom/xdg/myproduct")


@posix_only
def test_default_home(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert product_config_dir("myproduct") == Path.home() / ".config" / "myproduct"


@posix_only
def test_windows_name_ignored_off_windows(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    got = product_config_dir("my_product", windows_name="MyProduct")
    assert got.name == "my_product"


@windows_only
def test_windows_uses_appdata_and_windows_name(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert product_config_dir("my_product", windows_name="MyProduct") == (
        tmp_path / "MyProduct"
    )
    assert product_config_dir("my_product") == tmp_path / "my_product"
