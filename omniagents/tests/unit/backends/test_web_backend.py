from omniagents.backends.web import WebBackend


def test_should_auto_open_defaults_true(monkeypatch):
    monkeypatch.delenv("OMNI_WEB_AUTO_OPEN", raising=False)
    assert WebBackend._should_auto_open(None) is True


def test_should_auto_open_respects_env(monkeypatch):
    monkeypatch.setenv("OMNI_WEB_AUTO_OPEN", "0")
    assert WebBackend._should_auto_open(None) is False


def test_should_auto_open_kwarg_overrides_env(monkeypatch):
    monkeypatch.setenv("OMNI_WEB_AUTO_OPEN", "0")
    assert WebBackend._should_auto_open(True) is True
    monkeypatch.setenv("OMNI_WEB_AUTO_OPEN", "1")
    assert WebBackend._should_auto_open(False) is False
    monkeypatch.delenv("OMNI_WEB_AUTO_OPEN", raising=False)
    assert WebBackend._should_auto_open(False) is False


def test_is_minimal_ui_defaults_false(monkeypatch):
    monkeypatch.delenv("OMNI_WEB_MINIMAL", raising=False)
    assert WebBackend._is_minimal_ui(None) is False


def test_is_minimal_ui_respects_env(monkeypatch):
    monkeypatch.setenv("OMNI_WEB_MINIMAL", "1")
    assert WebBackend._is_minimal_ui(None) is True
    monkeypatch.setenv("OMNI_WEB_MINIMAL", "false")
    assert WebBackend._is_minimal_ui(None) is False


def test_is_minimal_ui_kwarg_overrides_env(monkeypatch):
    monkeypatch.setenv("OMNI_WEB_MINIMAL", "1")
    assert WebBackend._is_minimal_ui(False) is False
    monkeypatch.setenv("OMNI_WEB_MINIMAL", "0")
    assert WebBackend._is_minimal_ui(True) is True
