from omniagents.core.sandbox.profiles import _inject_egress_proxy

PROXY = "http://factory:t0ken@egress:3128"


def test_quicksand_sandboxes_reach_only_the_forwarded_proxy(monkeypatch):
    monkeypatch.setenv("OMNIAGENTS_SANDBOX_PROXY", PROXY)
    profile = {
        "client": {"type": "quicksand"},
        "options": {"network_mode": "full", "host_services": {7300: 7300}},
        "manifest": {"environment": {"value": {"HTTPS_PROXY": "http://elsewhere:1"}}},
    }
    _inject_egress_proxy(profile)
    assert profile["options"]["network_mode"] == "mounts_only"
    assert profile["options"]["host_services"] == {7300: 7300, 3128: "egress:3128"}
    values = profile["manifest"]["environment"]["value"]
    assert values["HTTPS_PROXY"] == "http://factory:t0ken@10.0.2.101:3128"
    assert values["NO_PROXY"] == "10.0.2.101,localhost,127.0.0.1"


def test_containers_get_the_proxy_and_nothing_changes_without_one(monkeypatch):
    profile = {"client": {"type": "docker"}}
    _inject_egress_proxy(profile)
    assert profile == {"client": {"type": "docker"}}
    monkeypatch.setenv("OMNIAGENTS_SANDBOX_PROXY", PROXY)
    _inject_egress_proxy(profile)
    assert profile["manifest"]["environment"]["value"]["https_proxy"] == PROXY
