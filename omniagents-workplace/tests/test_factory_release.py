import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from omniagents_workplace.deployments import releases


@pytest.fixture
def controller(tmp_path, monkeypatch):
    sys.modules["release_evidence"] = releases
    path = Path(__file__).parents[1] / "scripts/factory_release_controller.py"
    spec = importlib.util.spec_from_file_location("factory_release_controller", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "releases"
    root.mkdir()
    cfg = dict(
        provider="github",
        repository="org/factory",
        factory_id="a" * 32,
        container="factory",
        network="net",
        architecture="personal",
        image_repository="registry/factory",
        framework_image="framework@sha256:" + "b" * 64,
        approval_file=str(root / "approval.json"),
        docker_config=str(tmp_path),
        policy_url="http://policy",
        runtime_env="/shared/factory.env",
        validate_command=["validate"],
        backup_command=["backup"],
        chat_check_command=["chat"],
        evaluation_command=["eval"],
        evaluation_version="test-v1",
        snapshot_command=["snapshot"],
    )
    for key in [
        "runtime_token_file",
        "runtime_control_token_file",
        "policy_token_file",
    ]:
        p = tmp_path / key
        p.write_text(key)
        cfg[key] = str(p)
    request = dict(REVISION="c" * 40, RUN_ID="123", IMAGE_DIGEST="sha256:" + "d" * 64)
    expected = dict(
        revision=request["REVISION"], image_digest=request["IMAGE_DIGEST"], run_id="123"
    )
    monkeypatch.setattr(module, "check", lambda *_: dict(expected))
    events = []
    state = dict(busy=0, new=False, fail_chat=False)
    before = dict(
        Image="old-image",
        Config={"Env": ["KEEP=1"], "Labels": {}, "User": "root"},
        HostConfig={
            "Mounts": [{"Type": "volume", "Source": "durable", "Target": "/data"}]
        },
        NetworkSettings={"Networks": {"net": {"IPAddress": "127.0.0.1"}}},
    )

    def docker(method, path, body=None):
        events.append((method, path, body))
        if path.startswith("/images/"):
            return {
                "Config": {
                    "Labels": {
                        "org.opencontainers.image.revision": request["REVISION"],
                        "org.omniagents.framework": cfg["framework_image"],
                    }
                }
            }
        if method == "GET":
            return json.loads(json.dumps(before))
        if path.startswith("/containers/create"):
            state["new"] = True
        if method == "DELETE":
            state["new"] = False
        return {}

    def http(url, token, method="GET", body=None):
        events.append((method, url, body))
        if url == "http://policy":
            return dict(enabled=True, provider="github", repository="org/factory")
        if url.endswith("/architecture"):
            return dict(
                release_protocol=1,
                busy=state["busy"],
                architecture="personal",
                version=request["REVISION"] if state["new"] else "old",
                release_paused=state["new"],
            )
        if method == "POST":
            return {"lease": "old-lease"}
        (root / "pending").unlink(missing_ok=True)
        return {"ok": True}

    def run(argv, **kwargs):
        events.append(("command", argv, None))
        if argv[0] == "chat" and state["fail_chat"]:
            raise RuntimeError("auth/chat failure")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module, "docker", docker)
    monkeypatch.setattr(module, "http", http)
    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    return SimpleNamespace(
        module=module,
        root=root,
        cfg=cfg,
        request=request,
        expected=expected,
        events=events,
        state=state,
    )


def approve(c):
    Path(c.cfg["approval_file"]).write_text(json.dumps(c.expected))


def test_passing_ci_cannot_activate_without_operator_approval(controller):
    c = controller
    c.module.activate(c.cfg, c.request, c.root)
    assert (
        json.loads((c.root / "state.json").read_text())["phase"] == "awaiting_approval"
    )
    assert not any("/containers/" in str(e[1]) for e in c.events)


def test_busy_factory_defers_without_stopping_or_backing_up(controller):
    c = controller
    approve(c)
    c.state["busy"] = 1
    c.module.activate(c.cfg, c.request, c.root)
    assert json.loads((c.root / "state.json").read_text())["phase"] == "deferred"
    assert not any("/stop" in str(e[1]) or e[1] == ["backup"] for e in c.events)


def test_activation_preserves_mounts_and_waits_for_authenticated_chat(controller):
    c = controller
    approve(c)
    c.module.activate(c.cfg, c.request, c.root)
    created = next(e[2] for e in c.events if str(e[1]).startswith("/containers/create"))
    assert created["HostConfig"]["Mounts"][0] == {
        "Type": "volume",
        "Source": "durable",
        "Target": "/data",
    }
    assert created["Image"].endswith("@" + c.request["IMAGE_DIGEST"])
    assert "/release/project.yml" in created["Cmd"][1]
    assert "FACTORY_PROMOTION_MODE=release" in created["Cmd"][1]
    chat = next(
        i for i, e in enumerate(c.events) if e[0] == "command" and e[1][0] == "chat"
    )
    unpause = next(
        i
        for i, e in enumerate(c.events)
        if e[0] == "DELETE" and str(e[1]).endswith("/release-lease")
    )
    assert chat < unpause
    assert json.loads((c.root / "state.json").read_text())["phase"] == "complete"
    assert (
        json.loads((c.root / "runtime.json").read_text())["revision"]
        == c.request["REVISION"]
    )


def test_failed_authenticated_chat_restores_previous_runtime_without_deleting_data(
    controller,
):
    c = controller
    approve(c)
    c.state["fail_chat"] = True
    prior = {"image": "previous@sha256:" + "1" * 64, "revision": "1" * 40}
    (c.root / "runtime.json").write_text(json.dumps(prior))
    with pytest.raises(RuntimeError, match="auth/chat"):
        c.module.activate(c.cfg, c.request, c.root)
    assert json.loads((c.root / "state.json").read_text())["phase"] == "rolled_back"
    assert json.loads((c.root / "runtime.json").read_text()) == prior
    assert any(
        e[0] == "POST" and str(e[1]).endswith("/rename?name=factory") for e in c.events
    )
    assert not any("/volumes/" in str(e[1]) for e in c.events)
    assert not (c.root / "pending").exists()


def test_missing_behavior_gate_never_stops_runtime(controller):
    c = controller
    approve(c)
    c.cfg.pop("evaluation_command")
    with pytest.raises(ValueError, match="evaluation gate"):
        c.module.activate(c.cfg, c.request, c.root)
    assert not any("/stop" in str(e[1]) for e in c.events)
    assert json.loads((c.root / "state.json").read_text())["phase"] == "approved"


def test_evaluation_failure_does_not_leave_auto_retry_deferred(controller, monkeypatch):
    c = controller
    approve(c)
    (c.root / "state.json").write_text(json.dumps({**c.expected, "phase": "deferred"}))

    def fail(argv, **kwargs):
        if argv[0] == "eval":
            raise RuntimeError("evaluation failed")

    monkeypatch.setattr(c.module.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="evaluation failed"):
        c.module.activate(c.cfg, c.request, c.root)
    assert json.loads((c.root / "state.json").read_text())["phase"] == "approved"
    assert not any("/stop" in str(e[1]) for e in c.events)
