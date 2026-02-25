def test_network_allowlist_adds_cap_and_env(monkeypatch, tmp_path):
    from omni_code import sandbox_cli

    monkeypatch.setenv("OPENAI_API_KEY", "abc")
    monkeypatch.delenv("OMNI_CODE_SANDBOX_OMNIAGENTS_HOME", raising=False)

    workspace = tmp_path / "ws"
    workspace.mkdir()

    config_dir = tmp_path / "cfg"
    monkeypatch.setattr(sandbox_cli, "get_config_dir", lambda: config_dir)
    monkeypatch.setattr(sandbox_cli.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(sandbox_cli, "_assert_port_available", lambda _: None)
    monkeypatch.setattr(sandbox_cli, "_docker_image_exists", lambda _: True)
    monkeypatch.setattr(sandbox_cli, "_sandbox_image_tag", lambda *_a, **_kw: "omni-code-sandbox:test")

    calls = []

    def fake_run(args, check=True, capture_output=False):
        calls.append(list(args))

        class Result:
            def __init__(self, returncode=0):
                self.returncode = returncode

        return Result(returncode=0)

    monkeypatch.setattr(sandbox_cli, "_run_process", fake_run)

    sandbox_cli.main(
        [
            "--workspace",
            str(workspace),
            "--ui",
            "none",
            "--mode",
            "server",
            "--port",
            "7777",
            "--network-allowlist",
            "foo.com,10.0.0.0/16,bar.com",
        ]
    )

    docker_run = next(cmd for cmd in calls if cmd[:2] == ["docker", "run"])
    assert "--cap-add" in docker_run
    cap_idx = docker_run.index("--cap-add")
    assert docker_run[cap_idx + 1] == "NET_ADMIN"
    assert "OMNI_SANDBOX_NETWORK_ALLOWLIST=foo.com,10.0.0.0/16,bar.com" in docker_run


def test_no_allowlist_omits_cap_and_env(monkeypatch, tmp_path):
    from omni_code import sandbox_cli

    monkeypatch.setenv("OPENAI_API_KEY", "abc")
    monkeypatch.delenv("OMNI_CODE_SANDBOX_OMNIAGENTS_HOME", raising=False)

    workspace = tmp_path / "ws"
    workspace.mkdir()

    config_dir = tmp_path / "cfg"
    monkeypatch.setattr(sandbox_cli, "get_config_dir", lambda: config_dir)
    monkeypatch.setattr(sandbox_cli.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(sandbox_cli, "_assert_port_available", lambda _: None)
    monkeypatch.setattr(sandbox_cli, "_docker_image_exists", lambda _: True)
    monkeypatch.setattr(sandbox_cli, "_sandbox_image_tag", lambda *_a, **_kw: "omni-code-sandbox:test")

    calls = []

    def fake_run(args, check=True, capture_output=False):
        calls.append(list(args))

        class Result:
            def __init__(self, returncode=0):
                self.returncode = returncode

        return Result(returncode=0)

    monkeypatch.setattr(sandbox_cli, "_run_process", fake_run)

    sandbox_cli.main(
        [
            "--workspace",
            str(workspace),
            "--ui",
            "none",
            "--mode",
            "server",
            "--port",
            "7777",
        ]
    )

    docker_run = next(cmd for cmd in calls if cmd[:2] == ["docker", "run"])
    assert "--cap-add" not in docker_run
    assert all("OMNI_SANDBOX_NETWORK_ALLOWLIST" not in arg for arg in docker_run)
