"""Tests for ContainerRoutingMixin — faked sandbox stack, no Docker."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import omniagents.core.sandbox.factory as factory
from omniagents.core.eval.container_routing import ContainerRoutingMixin
from omniagents.core.eval.local_workspace import LocalWorkspaceEnvironment


class _FakeSession:
    def __init__(self):
        self.state = SimpleNamespace(container_id="cafebabe1234")
        self.execed = []
        self.started = False
        self.stopped = False
        self.closed = False
        self.exec_results = {}

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def aclose(self):
        self.closed = True

    async def exec(self, cmd):
        self.execed.append(cmd)
        return self.exec_results.get(
            cmd, SimpleNamespace(exit_code=0, stdout=b"", stderr=b"")
        )


class _FakeClient:
    def __init__(self):
        self.created_with = None
        self.session = _FakeSession()

    async def create(self, *, manifest, options, snapshot):
        self.created_with = {"manifest": manifest, "options": options}
        return self.session


def _resolver_for(raw_profile, init=None):
    def resolve(*, workspace_dir, explicit_path):
        return SimpleNamespace(
            raw=dict(raw_profile), init=list(init or []), sources=[explicit_path]
        )

    return resolve


@pytest.fixture
def fake_factory(monkeypatch):
    client = _FakeClient()
    captured = {}

    def fake_build_sandbox_client(raw):
        captured["client_raw"] = raw
        return client, {"opts": True}

    def fake_build_manifest(raw):
        captured["manifest_raw"] = raw

        class _FakeManifest(SimpleNamespace):
            def model_copy(self, *, update=None, deep=False):
                data = dict(self.__dict__)
                data.update(update or {})
                return _FakeManifest(**data)

        return _FakeManifest(users=[])

    monkeypatch.setattr(factory, "build_sandbox_client", fake_build_sandbox_client)
    monkeypatch.setattr(factory, "build_manifest", fake_build_manifest)
    return client, captured


def _env_class(resolver, default_profile_path=None):
    class Env(ContainerRoutingMixin, LocalWorkspaceEnvironment):
        profile_resolver = staticmethod(resolver)

    Env.default_profile_path = default_profile_path
    return Env


def test_container_setup_mounts_workspace_and_runs_init(tmp_path, fake_factory):
    client, captured = fake_factory
    profile_file = tmp_path / "p.yml"
    profile_file.write_text("client: {type: docker}\n")
    resolver = _resolver_for(
        {"client": {"type": "docker"}, "options": {"image": "img:1"}, "run_as": "dev"},
        init=["echo init-1"],
    )
    Env = _env_class(resolver)
    env = Env(workspace_cache_dir=tmp_path / "cache")

    ctx = asyncio.run(
        env.setup({"environment": {"profile": str(profile_file)}})
    )
    try:
        raw = captured["client_raw"]
        assert raw["manifest"]["root"] == "/workspace"
        assert raw["manifest"]["entries"]["."] == {
            "type": "local_dir",
            "src": ctx["workspace_root"],
        }
        assert client.session.started
        assert client.session.execed == ["echo init-1"]
        assert env._sessions[ctx.session_id]["run_as"] == "dev"
    finally:
        asyncio.run(env.teardown(ctx))
    assert client.session.stopped and client.session.closed
    assert ctx.session_id not in env._sessions


def test_image_override_uses_default_profile(tmp_path, fake_factory):
    client, captured = fake_factory
    default_profile = tmp_path / "default.yml"
    default_profile.write_text("client: {type: docker}\n")
    resolver = _resolver_for({"client": {"type": "docker"}, "options": {"image": "base"}})
    Env = _env_class(resolver, default_profile_path=default_profile)
    env = Env(workspace_cache_dir=tmp_path / "cache")

    ctx = asyncio.run(env.setup({"environment": {"image": "override:9"}}))
    try:
        assert captured["client_raw"]["options"]["image"] == "override:9"
    finally:
        asyncio.run(env.teardown(ctx))


def test_image_without_default_profile_errors(tmp_path, fake_factory):
    resolver = _resolver_for({"client": {"type": "docker"}})
    Env = _env_class(resolver, default_profile_path=None)
    env = Env(workspace_cache_dir=tmp_path / "cache")

    with pytest.raises(RuntimeError, match="default_profile_path"):
        asyncio.run(env.setup({"environment": {"image": "x"}}))


def test_init_failure_aborts_with_command(tmp_path, fake_factory):
    client, _ = fake_factory
    client.session.exec_results["bad-cmd"] = SimpleNamespace(
        exit_code=7, stdout=b"", stderr=b"boom"
    )
    profile_file = tmp_path / "p.yml"
    profile_file.write_text("client: {type: docker}\n")
    resolver = _resolver_for({"client": {"type": "docker"}}, init=["bad-cmd"])
    Env = _env_class(resolver)
    env = Env(workspace_cache_dir=tmp_path / "cache")

    with pytest.raises(RuntimeError, match="bad-cmd"):
        asyncio.run(env.setup({"environment": {"profile": str(profile_file)}}))


def test_prepare_spec_attaches_session_and_container_root(tmp_path, fake_factory):
    client, _ = fake_factory
    profile_file = tmp_path / "p.yml"
    profile_file.write_text("client: {type: docker}\n")
    resolver = _resolver_for({"client": {"type": "docker"}, "run_as": "dev"})
    Env = _env_class(resolver)
    env = Env(workspace_cache_dir=tmp_path / "cache")

    ctx = asyncio.run(env.setup({"environment": {"profile": str(profile_file)}}))
    try:
        spec = SimpleNamespace(
            sandbox_use_sandbox_agent=False,
            sandbox_run_as=None,
            sandbox_run_config=None,
            context={"workspace_root": ctx["workspace_root"]},
        )
        asyncio.run(env.prepare_spec(spec, ctx))
        assert spec.sandbox_use_sandbox_agent is True
        assert spec.sandbox_run_as == "dev"
        assert spec.sandbox_run_config.session is client.session
        assert spec.context["workspace_root"] == "/workspace"
    finally:
        asyncio.run(env.teardown(ctx))


def test_command_and_file_primitives_route_into_container(tmp_path, fake_factory):
    client, _ = fake_factory
    profile_file = tmp_path / "p.yml"
    profile_file.write_text("client: {type: docker}\n")
    resolver = _resolver_for({"client": {"type": "docker"}})
    Env = _env_class(resolver)
    env = Env(workspace_cache_dir=tmp_path / "cache")

    ctx = asyncio.run(env.setup({"environment": {"profile": str(profile_file)}}))
    try:
        client.session.exec_results["pytest -q"] = SimpleNamespace(
            exit_code=1, stdout=b"1 failed", stderr=b""
        )
        code, out, err = env.run_command(ctx, "pytest -q")
        assert (code, out) == (1, "1 failed")

        client.session.exec_results["test -e /workspace/a.py"] = SimpleNamespace(
            exit_code=0, stdout=b"", stderr=b""
        )
        assert asyncio.run(env._path_exists(ctx, "a.py")) is True

        client.session.exec_results["cat /workspace/a.py"] = SimpleNamespace(
            exit_code=0, stdout=b"print(1)", stderr=b""
        )
        assert asyncio.run(env._read_file(ctx, "a.py")) == "print(1)"

        env_vars = env.get_env_vars(ctx)
        assert env_vars["PWD"] == "/workspace"
    finally:
        asyncio.run(env.teardown(ctx))


def test_host_only_scenario_untouched(tmp_path, fake_factory):
    resolver = _resolver_for({"client": {"type": "docker"}})
    Env = _env_class(resolver)
    env = Env(workspace_cache_dir=tmp_path / "cache")

    ctx = asyncio.run(env.setup({}))
    try:
        assert ctx.session_id not in env._sessions
        code, out, _ = env.run_command(ctx, "echo host")
        assert (code, out.strip()) == (0, "host")
        assert env.get_env_vars(ctx)["PWD"] == ctx["workspace_root"]
    finally:
        asyncio.run(env.teardown(ctx))
