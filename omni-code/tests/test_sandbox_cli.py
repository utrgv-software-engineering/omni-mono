import pytest


def test_sandbox_cli_local_ui_json_output(monkeypatch, tmp_path, capsys):
    import json

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

    docker_calls = []

    def fake_run_process(args, check=True, capture_output=False):
        docker_calls.append((list(args), capture_output))

        class Result:
            def __init__(self, stdout=""):
                self.returncode = 0
                self.stdout = stdout

        if args[:2] == ["docker", "run"] and capture_output:
            return Result(stdout="abc123\n")
        return Result()

    monkeypatch.setattr(sandbox_cli, "_run_process", fake_run_process)

    ui_calls = []

    def fake_run_local_ui(*, ws_url, ui_host, ui_port, auth_token, auth_token_env):
        ui_calls.append(
            {
                "ws_url": ws_url,
                "ui_host": ui_host,
                "ui_port": ui_port,
                "auth_token": auth_token,
                "auth_token_env": auth_token_env,
            }
        )

    monkeypatch.setattr(sandbox_cli, "_run_local_ui", fake_run_local_ui)

    sandbox_cli.main(
        [
            "--workspace",
            str(workspace),
            "--ui",
            "local",
            "--mode",
            "server",
            "--port",
            "7777",
            "--ui-port",
            "8888",
            "--enable-code-server",
            "--code-server-port",
            "9999",
            "--enable-vnc",
            "--vnc-port",
            "9998",
            "--output",
            "json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())

    assert payload["sandbox_url"] == "http://localhost:7777"
    assert payload["ws_url"] == "ws://localhost:7777/ws"
    assert payload["ui_url"] == "http://localhost:8888"
    assert payload["code_server_url"] == "http://localhost:9999"
    assert payload["novnc_url"].startswith("http://localhost:9998/")
    assert payload["container_id"] == "abc123"

    assert ui_calls
    assert ui_calls[0]["ws_url"] == "ws://localhost:7777/ws"
    assert ui_calls[0]["ui_host"] == "0.0.0.0"
    assert ui_calls[0]["ui_port"] == 8888


def test_sandbox_cli_builds_when_missing_and_runs(monkeypatch, tmp_path, capsys):
    from omni_code import sandbox_cli

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".env").write_text("OPENAI_API_KEY=x\n", encoding="utf-8")

    config_dir = tmp_path / "cfg"
    monkeypatch.setattr(sandbox_cli, "get_config_dir", lambda: config_dir)
    monkeypatch.setattr(sandbox_cli.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(sandbox_cli.os, "getuid", lambda: 123)
    monkeypatch.setattr(sandbox_cli.os, "getgid", lambda: 456)
    monkeypatch.setattr(sandbox_cli, "_assert_port_available", lambda _: None)
    monkeypatch.setenv("OPENAI_API_KEY", "abc")
    monkeypatch.delenv("OMNI_CODE_SANDBOX_OMNIAGENTS_HOME", raising=False)

    calls = []

    def fake_run(args, check=True, capture_output=False):
        calls.append((list(args), check, capture_output))

        class Result:
            def __init__(self, returncode=0):
                self.returncode = returncode

        if args[:3] == ["docker", "image", "inspect"]:
            return Result(returncode=1)
        return Result(returncode=0)

    monkeypatch.setattr(sandbox_cli, "_run_process", fake_run)
    monkeypatch.setattr(sandbox_cli, "_sandbox_image_tag", lambda *_a, **_kw: "omni-code-sandbox:test")

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

    out = capsys.readouterr().out
    assert "http://localhost:7777" in out

    assert calls[0][0][:3] == ["docker", "image", "inspect"]
    assert calls[1][0][:3] == ["docker", "build", "-t"]
    assert calls[-1][0][:2] == ["docker", "run"]

    run_cmd = calls[-1][0]
    assert "--env-file" in run_cmd
    assert str(workspace / ".env") in run_cmd
    assert "-e" in run_cmd
    assert "LOCAL_UID=123" in run_cmd
    assert "LOCAL_GID=456" in run_cmd
    assert "HOME=/home/user" in run_cmd
    assert "OMNIAGENTS_HOME=/home/user/.omniagents" in run_cmd
    assert "XDG_CONFIG_HOME=/home/user/.config" in run_cmd
    assert f"{config_dir / 'sandbox' / 'omniagents'}:/home/user/.omniagents" in run_cmd
    assert "omni" in run_cmd


def test_sandbox_cli_skips_build_when_image_exists(monkeypatch, tmp_path):
    from omni_code import sandbox_cli
    monkeypatch.setenv("OPENAI_API_KEY", "abc")
    monkeypatch.delenv("OMNI_CODE_SANDBOX_OMNIAGENTS_HOME", raising=False)

    workspace = tmp_path / "ws"
    workspace.mkdir()

    config_dir = tmp_path / "cfg"
    monkeypatch.setattr(sandbox_cli, "get_config_dir", lambda: config_dir)
    monkeypatch.setattr(sandbox_cli.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(sandbox_cli, "_assert_port_available", lambda _: None)

    calls = []

    def fake_run(args, check=True, capture_output=False):
        calls.append(list(args))

        class Result:
            def __init__(self, returncode=0):
                self.returncode = returncode

        return Result(returncode=0)

    monkeypatch.setattr(sandbox_cli, "_run_process", fake_run)
    monkeypatch.setattr(sandbox_cli, "_sandbox_image_tag", lambda *_a, **_kw: "omni-code-sandbox:test")

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

    assert calls[0][:3] == ["docker", "image", "inspect"]
    assert all(cmd[:3] != ["docker", "build", "-t"] for cmd in calls)
    assert any(cmd[:2] == ["docker", "run"] for cmd in calls)


def test_sandbox_cli_rejects_missing_workspace(monkeypatch, tmp_path):
    from omni_code import sandbox_cli

    monkeypatch.setattr(sandbox_cli.shutil, "which", lambda _: "/usr/bin/docker")

    with pytest.raises(SystemExit) as exc:
        sandbox_cli.main(["--workspace", str(tmp_path / "missing")])

    assert exc.value.code == 1


def test_sandbox_cli_detach_print_url(monkeypatch, tmp_path, capsys):
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
        calls.append((list(args), capture_output))

        class Result:
            def __init__(self):
                self.returncode = 0
                self.stdout = "abc123\n"

        return Result()

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
            "--detach",
            "--print-url",
        ]
    )

    out = capsys.readouterr().out
    assert out.strip() == "http://localhost:7777"

    run_cmd, capture = next(call for call in calls if call[0][:2] == ["docker", "run"])
    assert run_cmd[:2] == ["docker", "run"]
    assert "-d" in run_cmd
    assert capture is True


def test_sandbox_cli_local_ui_runs_proxy(monkeypatch, tmp_path, capsys):
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

    docker_calls = []

    def fake_run_process(args, check=True, capture_output=False):
        docker_calls.append((list(args), capture_output))

        class Result:
            def __init__(self, stdout=""):
                self.returncode = 0
                self.stdout = stdout

        if args[:2] == ["docker", "run"] and capture_output:
            return Result(stdout="abc123\n")
        return Result()

    monkeypatch.setattr(sandbox_cli, "_run_process", fake_run_process)

    ui_calls = []

    def fake_run_local_ui(*, ws_url, ui_host, ui_port, auth_token, auth_token_env):
        ui_calls.append(
            {
                "ws_url": ws_url,
                "ui_host": ui_host,
                "ui_port": ui_port,
                "auth_token": auth_token,
                "auth_token_env": auth_token_env,
            }
        )

    monkeypatch.setattr(sandbox_cli, "_run_local_ui", fake_run_local_ui)

    sandbox_cli.main(
        [
            "--workspace",
            str(workspace),
            "--ui",
            "local",
            "--mode",
            "server",
            "--port",
            "7777",
            "--ui-port",
            "8888",
            "--auth-token",
            "secret",
        ]
    )

    out = capsys.readouterr().out
    assert "Omni Code web UI: http://localhost:8888" in out

    assert ui_calls
    assert ui_calls[0]["ws_url"] == "ws://localhost:7777/ws"
    assert ui_calls[0]["ui_host"] == "0.0.0.0"
    assert ui_calls[0]["ui_port"] == 8888
    assert ui_calls[0]["auth_token"] == "secret"
    assert ui_calls[0]["auth_token_env"] == "OMNI_SANDBOX_REMOTE_TOKEN"

    docker_run = next(cmd for cmd, _ in docker_calls if cmd[:2] == ["docker", "run"])
    assert "-d" in docker_run
    assert ["--mode", "server"] == docker_run[docker_run.index("omni") + 1 : docker_run.index("omni") + 3]
    assert "--auth-token" in docker_run
    assert "secret" in docker_run

    assert f"{config_dir / 'sandbox' / 'omniagents'}:/home/user/.omniagents" in docker_run

    assert any(cmd[:2] == ["docker", "stop"] and cmd[2] == "abc123" for cmd, _ in docker_calls)


def test_sandbox_cli_ui_container_forces_web_mode(monkeypatch, tmp_path):
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
            "container",
            "--mode",
            "server",
            "--port",
            "7777",
        ]
    )

    docker_run = next(cmd for cmd in calls if cmd[:2] == ["docker", "run"])
    assert ["--mode", "web"] == docker_run[docker_run.index("omni") + 1 : docker_run.index("omni") + 3]


def test_sandbox_cli_enable_code_server_adds_port_and_env(monkeypatch, tmp_path):
    from omni_code import sandbox_cli

    monkeypatch.setenv("OPENAI_API_KEY", "abc")
    monkeypatch.delenv("OMNI_CODE_SANDBOX_OMNIAGENTS_HOME", raising=False)

    workspace = tmp_path / "ws"
    workspace.mkdir()

    config_dir = tmp_path / "cfg"
    monkeypatch.setattr(sandbox_cli, "get_config_dir", lambda: config_dir)
    monkeypatch.setattr(sandbox_cli.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(sandbox_cli, "_docker_image_exists", lambda _: True)
    monkeypatch.setattr(sandbox_cli, "_sandbox_image_tag", lambda *_a, **_kw: "omni-code-sandbox:test")

    asserted = []

    def fake_assert_port_available(port: int) -> None:
        asserted.append(port)

    monkeypatch.setattr(sandbox_cli, "_assert_port_available", fake_assert_port_available)

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
            "--enable-code-server",
            "--code-server-port",
            "9999",
        ]
    )

    docker_run = next(cmd for cmd in calls if cmd[:2] == ["docker", "run"])
    assert f"9999:{sandbox_cli.CODE_SERVER_CONTAINER_PORT}" in docker_run
    assert "OMNI_CODE_CODE_SERVER=1" in docker_run
    assert 7777 in asserted
    assert 9999 in asserted
