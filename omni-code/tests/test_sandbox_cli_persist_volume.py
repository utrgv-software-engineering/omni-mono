def test_sandbox_cli_persist_volume_mounts_and_creates(monkeypatch, tmp_path):
    from omni_code import sandbox_cli

    monkeypatch.setenv("OPENAI_API_KEY", "abc")

    workspace = tmp_path / "ws"
    workspace.mkdir()

    config_dir = tmp_path / "cfg"
    monkeypatch.setattr(sandbox_cli, "get_config_dir", lambda: config_dir)
    monkeypatch.setattr(sandbox_cli.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(sandbox_cli, "_assert_port_available", lambda _: None)
    monkeypatch.setattr(sandbox_cli, "_docker_image_exists", lambda _: True)
    monkeypatch.setattr(sandbox_cli, "_sandbox_image_tag", lambda: "omni-code-sandbox:test")

    calls = []

    def fake_run(args, check=True, capture_output=False):
        calls.append(list(args))

        class Result:
            def __init__(self, returncode=0):
                self.returncode = returncode

        if args[:3] == ["docker", "volume", "inspect"]:
            return Result(returncode=1)
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
            "--persist-volume",
            "omni-test-creds:/home/user/.azure",
        ]
    )

    assert any(cmd[:3] == ["docker", "volume", "inspect"] for cmd in calls)
    assert any(cmd[:3] == ["docker", "volume", "create"] for cmd in calls)

    docker_run = next(cmd for cmd in calls if cmd[:2] == ["docker", "run"])
    assert "omni-test-creds:/home/user/.azure" in docker_run

