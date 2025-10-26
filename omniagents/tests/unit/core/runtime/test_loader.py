import pytest

from omniagents.core.runtime.loader import is_backend_available, load_backend


def test_load_backend_uses_entry_points_select(monkeypatch):
    backend = object()

    class DummyEntryPoint:
        name = "cli"

        def load(self):
            return backend

    class DummyEntryPoints:
        def select(self, group):
            assert group == "omniagents.backends"
            return [DummyEntryPoint()]

    monkeypatch.setattr(
        "omniagents.core.runtime.loader.importlib.metadata.entry_points",
        lambda: DummyEntryPoints(),
    )
    monkeypatch.setattr(
        "omniagents.core.runtime.loader.importlib.import_module",
        lambda name: pytest.fail("should not import"),
    )

    assert load_backend("cli") is backend


def test_load_backend_falls_back_to_alias_mapping(monkeypatch):
    class EmptyEntryPoints:
        def select(self, group):
            return []

    backend_class = type("Backend", (), {})
    module = type("Module", (), {"FletBackend": backend_class})

    monkeypatch.setattr(
        "omniagents.core.runtime.loader.importlib.metadata.entry_points",
        lambda: EmptyEntryPoints(),
    )
    monkeypatch.setattr(
        "omniagents.core.runtime.loader.importlib.import_module",
        lambda name: module,
    )

    assert load_backend("gui") is backend_class


def test_load_backend_returns_none_when_missing(monkeypatch):
    class EmptyEntryPoints:
        def select(self, group):
            return []

    monkeypatch.setattr(
        "omniagents.core.runtime.loader.importlib.metadata.entry_points",
        lambda: EmptyEntryPoints(),
    )
    monkeypatch.setattr(
        "omniagents.core.runtime.loader.importlib.import_module",
        lambda name: (_ for _ in ()).throw(ImportError("missing")),
    )

    assert load_backend("unknown") is None


def test_is_backend_available(monkeypatch):
    monkeypatch.setattr(
        "omniagents.core.runtime.loader.load_backend", lambda name: object()
    )
    assert is_backend_available("cli") is True

    monkeypatch.setattr(
        "omniagents.core.runtime.loader.load_backend", lambda name: None
    )
    assert is_backend_available("missing") is False
