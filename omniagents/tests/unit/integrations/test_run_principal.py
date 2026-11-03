"""An integration's runs act as the principal the server's own token authenticates as."""

import asyncio
from types import SimpleNamespace

from omniagents.integrations import server as integrations_server
from omniagents.rpc.auth import ConnectionAuthenticator


def _app(auth_token):
    spec = SimpleNamespace(security_config={})
    authenticator = ConnectionAuthenticator.from_spec(spec, auth_token=auth_token)
    return SimpleNamespace(
        state=SimpleNamespace(connection_authenticator=authenticator)
    )


def test_a_server_without_a_token_starts_runs_as_before(monkeypatch):
    assert (
        asyncio.run(integrations_server._server_principal(_app(None), None)()) is None
    )


def test_factory_machine_is_attached_at_startup_and_detached_at_shutdown(
    monkeypatch, tmp_path
):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, Mock
    from fastapi import FastAPI

    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path))
    sandbox = SimpleNamespace(register=Mock())
    opened, detached = [], []

    @asynccontextmanager
    async def environment(label, *, state_path):
        opened.append((label, state_path))
        yield sandbox
        detached.append(label)

    resources = object()
    app = FastAPI()
    project = SimpleNamespace(product=SimpleNamespace(slug="factory"))
    principal = AsyncMock(return_value=SimpleNamespace(user_id="token_user"))
    integrations_server._attach_factory_workspace(
        app, project, resources, SimpleNamespace(sandbox=environment), principal
    )

    async def exercise():
        await app.router.on_startup[0]()
        assert not detached
        await app.router.on_shutdown[-1]()

    asyncio.run(exercise())
    assert opened == [
        ("factory", tmp_path / "integrations/factory.personal-sandbox.json")
    ]
    assert detached == ["factory"]
    sandbox.register.assert_called_once_with(resources, owner_user_id="token_user")
