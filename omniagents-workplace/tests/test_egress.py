"""The egress proxy, and the allowlists Workplace keeps for it."""

import asyncio
import base64
import json

import pytest
from fastapi.testclient import TestClient

from omniagents_workplace.accounts import SESSION_COOKIE
from omniagents_workplace.egress import covered
from omniagents_workplace.egress_proxy import EgressProxy, allowed
from omniagents_workplace.installation import Installation
from omniagents_workplace.installation_server import create_installation_app
from test_installation import identity, prefix, project


def test_allowlist_entries():
    assert allowed("api.openai.com", 443, ["api.openai.com"])
    assert allowed("api.openai.com", 80, ["api.openai.com"])
    assert not allowed("api.openai.com", 8443, ["api.openai.com"])
    assert allowed("x.openai.azure.com", 443, ["*.openai.azure.com"])
    assert not allowed("openai.azure.com", 443, ["*.openai.azure.com"])
    assert not allowed("evilopenai.azure.com", 443, ["*.openai.azure.com"])
    assert allowed("pypi.org", 8443, ["pypi.org:8443"])
    assert not allowed("pypi.org", 443, ["pypi.org:8443"])
    assert not allowed("anything.example", 443, [])
    assert covered("x.openai.azure.com", ["*.openai.azure.com"])
    assert covered("*.eastus.openai.azure.com", ["*.openai.azure.com"])
    assert not covered("example.com", ["*.openai.azure.com"])


async def _upstream():
    async def handle(reader, writer):
        data = await reader.read(65536)
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
        )
        writer.write(b"" if b"Proxy-Authorization" not in data else b"LEAKED")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _ask(port, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(65536), 5)
    if data.startswith(b"HTTP/1.1 200 Connection established"):
        writer.write(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        await writer.drain()
        data += await asyncio.wait_for(reader.read(65536), 5)
    writer.close()
    return data


def auth(user, token):
    return base64.b64encode(f"{user}:{token}".encode()).decode()


@pytest.mark.asyncio
async def test_the_proxy_lets_factories_reach_only_their_allowlist(tmp_path):
    upstream, upstream_port = await _upstream()
    config = tmp_path / "egress.json"
    denials = tmp_path / "denials.jsonl"
    config.write_text(
        json.dumps(
            {
                "deployment": [f"127.0.0.1:{upstream_port}"],
                "factories": {
                    "open": {"token": "t1", "name": "Open", "allow": None},
                    "narrow": {
                        "token": "t2",
                        "name": "Narrow",
                        "allow": ["only.example"],
                    },
                },
            }
        )
    )
    proxy = EgressProxy(config, denials)
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    target = f"127.0.0.1:{upstream_port}"
    try:
        nobody = await _ask(port, f"CONNECT {target} HTTP/1.1\r\n\r\n".encode())
        assert nobody.startswith(b"HTTP/1.1 407")
        wrong = await _ask(
            port,
            f"CONNECT {target} HTTP/1.1\r\nProxy-Authorization: Basic {auth('open', 'nope')}\r\n\r\n".encode(),
        )
        assert wrong.startswith(b"HTTP/1.1 407")
        tunnelled = await _ask(
            port,
            f"CONNECT {target} HTTP/1.1\r\nProxy-Authorization: Basic {auth('open', 't1')}\r\n\r\n".encode(),
        )
        assert tunnelled.endswith(b"ok")
        plain = await _ask(
            port,
            f"GET http://{target}/x HTTP/1.1\r\nHost: {target}\r\nProxy-Authorization: Basic {auth('open', 't1')}\r\n\r\n".encode(),
        )
        assert plain.startswith(b"HTTP/1.1 200") and b"LEAKED" not in plain
        # The narrow factory's own list does not include the target.
        refused = await _ask(
            port,
            f"CONNECT {target} HTTP/1.1\r\nProxy-Authorization: Basic {auth('narrow', 't2')}\r\n\r\n".encode(),
        )
        assert refused.startswith(b"HTTP/1.1 403")
        elsewhere = await _ask(
            port,
            f"CONNECT example.com:443 HTTP/1.1\r\nProxy-Authorization: Basic {auth('open', 't1')}\r\n\r\n".encode(),
        )
        assert elsewhere.startswith(b"HTTP/1.1 403")
    finally:
        server.close()
        upstream.close()
    recorded = [json.loads(line) for line in denials.read_text().splitlines()]
    assert [(d["factory"], d["host"]) for d in recorded] == [
        ("narrow", "127.0.0.1"),
        ("open", "example.com"),
    ]


@pytest.fixture
def egress(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKPLACE_POLICY", "strict")
    monkeypatch.setenv("WORKPLACE_EGRESS_PROXY", "http://egress:3128")
    monkeypatch.setenv("WORKPLACE_EGRESS_CONFIG", str(tmp_path / "egress.json"))
    monkeypatch.setenv("WORKPLACE_EGRESS_DENIALS", str(tmp_path / "denials.jsonl"))
    i = Installation(tmp_path / "installation", base_url="http://testserver")
    owner = identity(i, "owner", True)
    i.set_governance_owners(owner, [owner.id])
    record = i.add(name="One", project=project(tmp_path, "one"), owner=owner)
    with TestClient(create_installation_app(i, static_dir=tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, i.accounts.start_session(owner))
        yield i, client, record, tmp_path


def test_allowlists_narrow_at_once_and_widen_by_decision(egress):
    i, client, record, tmp_path = egress
    url = "/workplace/api/installation/egress"
    widened = client.put(
        url,
        json={
            "allow": ["*.openai.azure.com", "pypi.org"],
            "reason": "Models and packages",
        },
    ).json()
    assert widened["pending"] is None and widened["deployment"] == [
        "*.openai.azure.com",
        "pypi.org",
    ]
    config = json.loads((tmp_path / "egress.json").read_text())
    assert config["deployment"] == ["*.openai.azure.com", "pypi.org"]
    assert config["factories"][record["id"]]["allow"] is None
    # Another administrator's widening waits for the governance owner.
    admin2 = identity(i, "admin2", True)
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(admin2))
    proposed = client.put(
        url, json={"allow": ["*.openai.azure.com", "pypi.org", "github.com"]}
    ).json()
    assert (
        proposed["pending"]["kind"] == "egress"
        and "github.com" not in proposed["deployment"]
    )
    narrowed = client.put(url, json={"allow": ["*.openai.azure.com"]}).json()
    assert narrowed["pending"] is None and narrowed["deployment"] == [
        "*.openai.azure.com"
    ]
    # A Confidential factory narrows within the deployment's list.
    client.cookies.set(
        SESSION_COOKIE, i.accounts.start_session(i.accounts.by_username("owner"))
    )
    factory = prefix(record) + "/workplace/api/factory"
    client.put(
        factory + "/profile",
        json={"classification": "confidential", "heightened_risk": True},
    )
    outside = client.put(factory + "/egress", json={"allow": ["example.com"]})
    assert outside.status_code == 422
    own = client.put(
        factory + "/egress", json={"allow": ["eastus.openai.azure.com"]}
    ).json()
    assert own["effective"] == ["eastus.openai.azure.com"]
    config = json.loads((tmp_path / "egress.json").read_text())
    assert config["factories"][record["id"]]["allow"] == ["eastus.openai.azure.com"]


def test_runtimes_are_given_the_proxy_for_themselves_and_their_sandboxes(egress):
    i, client, record, tmp_path = egress
    w, _ = i.instances[record["id"]]
    token = w.egress.token()
    env = w.egress.runtime_environment(["workplace"])
    proxy = f"http://{record['id']}:{token}@egress:3128"
    assert env["HTTPS_PROXY"] == env["OMNIAGENTS_SANDBOX_PROXY"] == proxy
    assert "workplace" in env["NO_PROXY"].split(",")


def test_denials_are_audited_and_confidential_ones_alert(egress):
    i, client, record, tmp_path = egress
    w, _ = i.instances[record["id"]]
    denial = {"at": 1.0, "factory": record["id"], "host": "example.com", "port": 443}
    (tmp_path / "denials.jsonl").write_text(json.dumps(denial) + "\n")
    offset, alerts = i.record_denials(0)
    assert (
        alerts == []
        and w.audit_log.events(event="egress.denied")[0]["host"] == "example.com"
    )
    w.store.set_meta("factory.profile", {"classification": "confidential"})
    with (tmp_path / "denials.jsonl").open("a") as out:
        out.write(json.dumps(denial) + "\n")
    offset, alerts = i.record_denials(offset)
    assert [name for name, _ in alerts] == ["One"]
    assert i.record_denials(offset) == (offset, [])
