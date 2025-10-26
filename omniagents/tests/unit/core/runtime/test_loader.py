import types

import pytest

from omniagents.core.runtime import loader
from omniagents.core.runtime.loader import is_backend_available, load_backend


def test_load_backend_returns_entry_point(monkeypatch):
    backend = object()

    class DummyEntryPoint:
        def __init__(self, name, value):
            self.name = name
            self._value = value

        def load(self):
            return self._value

    class DummyEntryPoints:
        def select(self, group):
            assert group == "omniagents.backends"
            return [DummyEntryPoint("cli", backend)]

    monkeypatch.setattr(
        loader.importlib.metadata, "entry_points", lambda: DummyEntryPoints()
    )
    monkeypatch.setattr(
        loader.importlib,
        "import_module",
        lambda name: pytest.fail("should not import"),
    )

    result = load_backend("cli")
    assert result is backend


def test_load_backend_imports_alias(monkeypatch):
    class DummyEntryPoints:
        def select(self, group):
            return []

    backend = object()

    def fake_import_module(name):
        assert name == "omniagents.backends.flet"
        return types.SimpleNamespace(FletBackend=backend)

    monkeypatch.setattr(
        loader.importlib.metadata, "entry_points", lambda: DummyEntryPoints()
    )
    monkeypatch.setattr(loader.importlib, "import_module", fake_import_module)

    result = load_backend("gui")
    assert result is backend


def test_load_backend_returns_none_when_missing(monkeypatch):
    class DummyEntryPoints:
        def select(self, group):
            return []

    def fail_import(name):
        raise ImportError("missing module")

    monkeypatch.setattr(
        loader.importlib.metadata, "entry_points", lambda: DummyEntryPoints()
    )
    monkeypatch.setattr(loader.importlib, "import_module", fail_import)

    assert load_backend("cli") is None


def test_is_backend_available(monkeypatch):
    monkeypatch.setattr(
        "omniagents.core.runtime.loader.load_backend", lambda name: object()
    )
    assert is_backend_available("cli") is True

    monkeypatch.setattr(
        "omniagents.core.runtime.loader.load_backend", lambda name: None
    )
    assert is_backend_available("missing") is False
