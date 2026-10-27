"""The run backend must pass the explicitly configured control credential."""

from types import SimpleNamespace


def test_server_backend_passes_separate_admin_token(monkeypatch):
    from omniagents.backends.server import ServerBackend
    from omniagents.backends.server import app
    import uvicorn

    seen = {}

    def build(config_path, **kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(app, "build_app", build)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.setenv("OMNIAGENTS_AGENT_HOST_CONTROL_TOKEN", "admin-test")
    ServerBackend().run(SimpleNamespace(), auth_token="ordinary-test")
    assert seen["auth_token"] == "ordinary-test"
    assert seen["agent_host_control_token"] == "admin-test"
