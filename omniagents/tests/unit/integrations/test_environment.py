from omniagents.integrations.environment import GitHubConfig, parse_github


def test_the_github_integration_names_the_repositories_runs_check_out(monkeypatch):
    monkeypatch.setenv("GITHUB_GIT_URL", "http://host.docker.internal:7300/github")
    config = parse_github(
        [
            {"type": "slack", "app_token": "x"},
            {
                "type": "github",
                "repositories": ["acme/api-service", "acme/webapp"],
                "git_url": "${GITHUB_GIT_URL:-}",
            },
        ]
    )
    assert config == GitHubConfig(
        repositories=("acme/api-service", "acme/webapp"),
        git_url="http://host.docker.internal:7300/github",
    )
    assert config.sources() == [
        {
            "kind": "git-remote",
            "repoUrl": "https://github.com/acme/api-service",
            "mountName": "api-service",
        },
        {
            "kind": "git-remote",
            "repoUrl": "https://github.com/acme/webapp",
            "mountName": "webapp",
        },
    ]
    # The sandbox's git sends github.com to the workplace; remotes stay GitHub's.
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert config.git_environment() == {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "url.http://host.docker.internal:7300/github/.insteadOf",
        "GIT_CONFIG_VALUE_0": "https://github.com/",
    }


def test_the_sandboxes_git_sends_its_token_to_the_git_url_alone(monkeypatch):
    import base64

    monkeypatch.setenv("GH_TOKEN", "ghp_from_env")
    config = GitHubConfig(
        repositories=("acme/api-service",), git_url="http://workplace:7300/github"
    )
    env = config.git_environment()
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_KEY_1"] == "http.http://workplace:7300/github/.extraHeader"
    assert (
        env["GIT_CONFIG_VALUE_1"]
        == "Authorization: Basic "
        + base64.b64encode(b"x-access-token:ghp_from_env").decode()
    )
    # A token given outright (an evaluation scenario's) is the one sent.
    given = GitHubConfig(
        repositories=("acme/api-service",),
        git_url="http://workplace:7300/github",
        token="ghp_given",
    )
    assert (
        base64.b64decode(given.git_environment()["GIT_CONFIG_VALUE_1"].split()[-1])
        == b"x-access-token:ghp_given"
    )


def test_without_git_url_the_sandbox_clones_from_github(monkeypatch):
    monkeypatch.delenv("GITHUB_GIT_URL", raising=False)
    config = parse_github(
        [
            {
                "type": "github",
                "repositories": ["acme/api-service"],
                "git_url": "${GITHUB_GIT_URL:-}",
            }
        ]
    )
    assert config.git_url is None
    assert config.git_environment() == {}
    # github.com's own clones authenticate with GH_TOKEN.
    assert config.sources()[0]["auth"] == {"tokenEnv": "GH_TOKEN"}
    assert parse_github([{"type": "slack"}]) is None


def test_a_runs_sandbox_takes_the_products_user_layers(monkeypatch, tmp_path):
    """A deployment's <config>/sandbox/default.yml (the network sandboxes join) applies."""
    import asyncio

    from omniagents.core.sandbox import profiles
    from omniagents.integrations.environment import RunEnvironments

    config = tmp_path / "config"
    (config / "sandbox").mkdir(parents=True)
    (config / "sandbox" / "default.yml").write_text(
        "client:\n  type: docker\n  network_mode: acme-production\n"
    )
    project = tmp_path / "sandbox_profile_default.yml"
    project.write_text("client:\n  type: docker\n  image: node:22-bookworm\n")
    resolved = {}
    original = profiles.resolve_profile

    def resolve(**kwargs):
        resolved.update(original(config_dir=config, **kwargs).raw)
        raise RuntimeError("resolved")

    monkeypatch.setattr(profiles, "resolve_profile", resolve)

    async def run():
        async with RunEnvironments(project, GitHubConfig()).sandbox():
            pass

    try:
        asyncio.run(run())
    except RuntimeError as exc:
        assert str(exc) == "resolved"
    assert resolved["client"] == {
        "type": "docker",
        "image": "node:22-bookworm",
        "network_mode": "acme-production",
    }


def test_library_mount_survives_sandbox_recreation(monkeypatch, tmp_path):
    import asyncio
    from omniagents.integrations.environment import RunEnvironments
    from omniagents.core.sandbox import profiles
    from omniagents.core.runtime import serve_harness

    monkeypatch.setenv("LIBRARY_FILES_HOST_PATH", str(tmp_path / "durable"))
    original = profiles.resolve_profile
    monkeypatch.setattr(
        profiles,
        "resolve_profile",
        lambda **kwargs: original(config_dir=tmp_path / "config", **kwargs),
    )
    path = tmp_path / "profile.yml"
    path.write_text("version: 1\nclient:\n  type: docker\noptions:\n  image: node:22\n")
    captured = []

    async def bring_up(**kwargs):
        captured.append(kwargs["profile"].raw["client"]["binds"])
        raise RuntimeError("inspected")

    monkeypatch.setattr(serve_harness, "bring_up_session", bring_up)

    async def run():
        for _ in range(2):
            try:
                async with RunEnvironments(path, None).sandbox():
                    pass
            except RuntimeError as exc:
                assert str(exc) == "inspected"

    asyncio.run(run())
    assert (
        captured
        == [[{"source": str(tmp_path / "durable"), "target": "/workspace/Library"}]] * 2
    )


def test_personal_workspace_is_reattached_and_detached_not_destroyed(
    monkeypatch, tmp_path
):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from omniagents.integrations.environment import RunEnvironments
    from omniagents.core.runtime import serve_harness, environment_provisioner
    from omniagents.core.sandbox import profiles

    profile = SimpleNamespace(
        raw={"client": {"type": "docker"}}, init=[], build=[], services=[]
    )
    monkeypatch.setattr(profiles, "resolve_profile", lambda **kwargs: profile)
    up = AsyncMock(
        return_value=SimpleNamespace(
            session=object(),
            started_services=[],
            manifest=SimpleNamespace(root="/workspace"),
            container_id="same",
        )
    )
    down = AsyncMock()
    monkeypatch.setattr(serve_harness, "bring_up_session", up)
    monkeypatch.setattr(serve_harness, "teardown_session", down)
    monkeypatch.setattr(
        environment_provisioner, "sandbox_capabilities", lambda *args: {}
    )
    state = tmp_path / "personal.json"

    async def run():
        for _ in range(2):
            async with RunEnvironments(tmp_path / "profile.yml", None).sandbox(
                "personal", state_path=state
            ):
                pass
        async with RunEnvironments(tmp_path / "profile.yml", None).sandbox(
            "evaluation"
        ):
            pass

    asyncio.run(run())
    assert [c.kwargs["state_path"] for c in up.await_args_list] == [state, state, None]
    assert [c.kwargs["durable"] for c in up.await_args_list] == [True, True, False]
    assert [c.kwargs["destroy"] for c in down.await_args_list] == [False, False, True]
    assert [c.kwargs["detach"] for c in down.await_args_list] == [True, True, False]
