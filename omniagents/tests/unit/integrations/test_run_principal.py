"""An integration's runs act as the principal the server's own token authenticates as."""

import asyncio
from types import SimpleNamespace

from omniagents.integrations import server as integrations_server
from omniagents.rpc.auth import ConnectionAuthenticator


class _Agent:
    def __init__(self):
        self.calls = []

    async def initialize(self):
        self.calls.append("initialize")

    def set_principal(self, principal):
        self.calls.append(("principal", principal.user_id))

    def run_streamed(self, _input):
        self.calls.append("run")
        return SimpleNamespace(stream_events=_no_events)

    def get_last_run_end(self):
        return None


async def _no_events():
    return
    yield


def _app(auth_token):
    spec = SimpleNamespace(security_config={})
    authenticator = ConnectionAuthenticator.from_spec(spec, auth_token=auth_token)
    return SimpleNamespace(
        state=SimpleNamespace(connection_authenticator=authenticator)
    )


def test_runs_are_owned_by_the_servers_principal(monkeypatch):
    agent = _Agent()

    async def create_unified_agent(spec, **kwargs):
        return agent

    import omniagents.core.agents.factory as factory
    import omniagents.core.project.runtime as project_runtime

    monkeypatch.setattr(factory, "create_unified_agent", create_unified_agent)
    monkeypatch.setattr(
        project_runtime,
        "build_spec_from_project",
        lambda project, key: SimpleNamespace(),
    )
    run = integrations_server._run_starter(
        SimpleNamespace(),
        None,
        None,
        integrations_server._server_principal(_app("t"), "t"),
    )
    asyncio.run(run("foreman", "s1", "hello"))
    assert agent.calls == ["initialize", ("principal", "token_user"), "run"]


def test_a_server_without_a_token_starts_runs_as_before(monkeypatch):
    assert (
        asyncio.run(integrations_server._server_principal(_app(None), None)()) is None
    )


def test_factory_workspace_is_attached_once_and_kept_for_all_runs(
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
        agents = []

        async def create(spec, **kwargs):
            assert kwargs["host_resources"] is resources
            agent = _Agent()
            agents.append(agent)
            return agent

        import omniagents.core.agents.factory as factory
        import omniagents.core.project.runtime as runtime

        monkeypatch.setattr(factory, "create_unified_agent", create)
        monkeypatch.setattr(
            runtime, "build_spec_from_project", lambda project, key: key
        )
        run = integrations_server._run_starter(project, resources, None, principal)
        await asyncio.gather(
            run("implementer", "s1", "change"), run("reviewer", "s2", "review")
        )
        assert len(agents) == 2
        assert not detached
        await app.router.on_shutdown[-1]()

    asyncio.run(exercise())
    assert opened == [
        ("factory", tmp_path / "integrations/factory.personal-sandbox.json")
    ]
    assert detached == ["factory"]
    sandbox.register.assert_called_once_with(resources, owner_user_id="token_user")
