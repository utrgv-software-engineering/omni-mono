"""Workflow jobs and previews reach the internet through the factory's egress proxy."""

from types import SimpleNamespace

from omniagents_workplace.deployments.previews import preview_proxy
from omniagents_workplace.github.act_runner import ActionsRunner
from omniagents_workplace.github.quicksand_space import (
    QuicksandSettings,
    QuicksandSpace,
)
from omniagents_workplace.github.spaces import ContainerSettings, DockerSpace

PROXY = "http://f1:t0ken@egress:3128"
ENV = {"HTTP_PROXY": PROXY, "HTTPS_PROXY": PROXY, "NO_PROXY": "localhost"}


def runner(proxy=True):
    r = ActionsRunner(SimpleNamespace(), "http://workplace")
    if proxy:
        r.proxy_environment = lambda: ENV
        r.proxy_network = "ha-production-ci"
    return r


def test_job_settings_carry_the_proxy_and_its_network():
    settings = ContainerSettings(image="runner", env={"A": "1"})
    proxied = runner().with_proxy(settings)
    assert proxied.proxy == ENV and proxied.proxy_network == "ha-production-ci"
    assert proxied.env == {"A": "1"}
    assert runner(proxy=False).with_proxy(settings) is settings


def test_a_job_container_gets_the_proxy_and_joins_its_network(tmp_path, monkeypatch):
    import subprocess

    calls = []

    def docker(self, *args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(DockerSpace, "_docker", docker)
    bare = tmp_path / "repo.git"
    subprocess.run(["git", "init", "--quiet", str(tmp_path / "src")], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path / "src"),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "--quiet",
            "--allow-empty",
            "-m",
            "x",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "clone", "--quiet", "--bare", str(tmp_path / "src"), str(bare)],
        check=True,
    )
    sha = subprocess.run(
        ["git", "-C", str(bare), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    space = DockerSpace(
        1, runner().with_proxy(ContainerSettings(image="runner")), "test"
    )
    assert space.prepare(bare, sha) is None
    started = next(c for c in calls if c[0] == "run")
    assert f"HTTPS_PROXY={PROXY}" in started
    assert ("network", "connect", "ha-production-ci", space.name) in calls


def test_quicksand_job_vms_reach_the_proxy_through_a_forward():
    space = QuicksandSpace(1, QuicksandSettings(image="runner.qsave", proxy=PROXY))
    assert space.proxy_url() == "http://f1:t0ken@10.0.2.101:3128"
    assert space.proxy_target() == "egress:3128"
    assert (
        QuicksandSpace(2, QuicksandSettings(image="runner.qsave")).proxy_url() is None
    )


def test_previews_build_and_run_with_the_proxy_but_reach_their_own_services_directly():
    w = SimpleNamespace(egress=SimpleNamespace(runtime_environment=lambda hosts: ENV))
    compose = "services:\n  web: {image: app}\n  db: {image: postgres}\n"
    proxy = preview_proxy(w, compose)
    assert proxy["HTTPS_PROXY"] == PROXY
    assert proxy["NO_PROXY"].split(",") == ["127.0.0.1", "db", "localhost", "web"]
    off = SimpleNamespace(egress=SimpleNamespace(runtime_environment=lambda hosts: {}))
    assert preview_proxy(off, compose) == {}
