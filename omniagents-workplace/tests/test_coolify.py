import json
import stat

import pytest
import yaml

from omniagents_workplace.deployments.coolify import prepare


def test_restart_keeps_credentials_and_storage(tmp_path):
    data = tmp_path / "docker"
    original = prepare(data, "docker", 7400)
    secrets = (data / ".env").read_bytes(), (data / "target").read_bytes()
    assert prepare(data, "docker", 7400) == original
    assert secrets == ((data / ".env").read_bytes(), (data / "target").read_bytes())
    assert stat.S_IMODE(data.stat().st_mode) == 0o700
    assert stat.S_IMODE((data / "access.json").stat().st_mode) == 0o600
    with pytest.raises(ValueError, match="different backend"):
        prepare(data, "quicksand", 7500)


def test_instances_have_separate_keys_volumes_and_networks(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    first, second = prepare(a, "docker", 7400), prepare(b, "quicksand", 7500)
    assert first["project"] != second["project"]
    first_cookie = next(
        line
        for line in (a / ".env").read_text().splitlines()
        if line.startswith("SESSION_COOKIE=")
    )
    second_cookie = next(
        line
        for line in (b / ".env").read_text().splitlines()
        if line.startswith("SESSION_COOKIE=")
    )
    assert first_cookie != second_cookie
    assert (a / ".dockerignore").read_text() == "*\n!Target.Dockerfile\n"
    assert (a / "target").read_bytes() != (b / "target").read_bytes()
    assert (
        json.loads((a / "access.json").read_text())["password"]
        != json.loads((b / "access.json").read_text())["password"]
    )
    docker = yaml.safe_load((a / "compose.yml").read_text())
    vm = yaml.safe_load((b / "compose.yml").read_text())
    assert all(
        p.startswith("127.0.0.1:") for p in docker["services"]["coolify"]["ports"]
    )
    assert all(p.startswith("0.0.0.0:") for p in vm["services"]["coolify"]["ports"])
    assert all("name" not in v for v in docker["volumes"].values())
    assert "ports" not in docker["services"]["target"]
    assert (
        "/var/run/docker.sock:/var/run/docker.sock"
        in vm["services"]["target"]["volumes"]
    )
    assert "/opt/omni-coolify/resources" in (b / ".env").read_text()
