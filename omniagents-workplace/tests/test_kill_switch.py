"""The kill switch: who may stop and resume, what a stop refuses, and the runtime kept in line."""

import json

import httpx
import pytest
import yaml

from omniagents_workplace.accounts import SESSION_COOKIE
from omniagents_workplace.kill_switch import KEY, UNREADABLE
from test_event_gateway import event, gateway, rows  # noqa: F401 - fixture
from test_factory_runtime import _workplace
from test_installation import identity, installation, prefix  # noqa: F401 - fixture


def stop(client, path, reason="drill"):
    return client.post(path, json={"reason": reason})


def test_factory_admin_stops_and_resumes_with_a_reason(installation):
    i, client, owner, other, a, b = installation
    base = prefix(a) + "/workplace/api/factory"
    assert client.get(base + "/stop").json()["stopped"] is None
    assert stop(client, base + "/stop", " ").status_code == 422
    stopped = stop(client, base + "/stop").json()
    assert stopped["stopped"]["stopped_by"] == "owner"
    assert stopped["stopped"]["reason"] == "drill"
    assert "stopped by owner: drill" in stopped["message"]
    # The other factory is untouched.
    assert (
        client.get(prefix(b) + "/workplace/api/factory/stop").json()["stopped"] is None
    )
    # Chat refuses, before reaching the runtime.
    sent = client.post(
        prefix(a) + "/workplace/api/personal/chats/main/messages",
        json={"text": "hello", "submission_id": "00000000-0000-0000-0000-000000000001"},
    )
    assert sent.status_code == 409 and "stopped" in sent.json()["detail"]
    # The installation never starts it while stopped.
    started = client.post(f"/workplace/api/factories/{a['id']}/start")
    assert started.status_code == 422 and "stopped" in started.json()["detail"]
    listed = {
        f["id"]: f for f in client.get("/workplace/api/factories").json()["factories"]
    }
    assert listed[a["id"]]["stopped"]["reason"] == "drill"
    assert stop(client, base + "/resume", "fixed").json()["stopped"] is None
    assert stop(client, base + "/resume", "again").status_code == 409


def test_members_see_the_stop_but_cannot_use_the_switch(installation):
    i, client, owner, other, a, b = installation
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(other))
    base = prefix(a) + "/workplace/api/factory"
    assert client.get(base + "/stop").status_code == 200
    assert stop(client, base + "/stop").status_code == 403


def test_an_installation_administrators_stop_needs_one_to_resume(installation):
    i, client, owner, other, a, b = installation
    i.grant(a["id"], other.id, "admin")
    base = prefix(a) + "/workplace/api/factory"
    assert stop(client, base + "/stop").status_code == 200
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(other))
    refused = stop(client, base + "/resume", "mine now")
    assert refused.status_code == 403
    # A factory administrator's own stop is theirs to resume.
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(owner))
    assert stop(client, base + "/resume", "fixed").status_code == 200
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(other))
    assert stop(client, base + "/stop").status_code == 200
    assert stop(client, base + "/resume", "fixed").status_code == 200


def test_stop_all_stops_every_factory_and_only_installation_admins_use_it(installation):
    i, client, owner, other, a, b = installation
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(other))
    assert stop(client, "/workplace/api/installation/stop").status_code == 403
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(owner))
    stopped = stop(client, "/workplace/api/installation/stop", "incident").json()
    assert "All factories are stopped by owner: incident" in stopped["message"]
    for record in (a, b):
        view = client.get(prefix(record) + "/workplace/api/factory/stop").json()
        assert view["stopped"]["scope"] == "installation"
        assert view["own"] is None
    # A factory's own resume cannot lift the stop of all factories.
    assert stop(client, prefix(a) + "/workplace/api/factory/resume").status_code == 409
    assert (
        stop(client, "/workplace/api/installation/resume", "over").json()["stopped"]
        is None
    )
    assert (
        client.get(prefix(a) + "/workplace/api/factory/stop").json()["stopped"] is None
    )
    assert stop(client, "/workplace/api/installation/resume", "over").status_code == 409


def test_unreadable_state_counts_as_stopped(tmp_path):
    w = _workplace(tmp_path, "http://runtime.invalid")

    def broken():
        raise RuntimeError("store unavailable")

    w.kill_switch.installation_state = broken
    assert w.kill_switch.state() == UNREADABLE
    w.close()


@pytest.mark.asyncio
async def test_gateway_skips_events_while_stopped_and_never_replays_them(
    gateway,
):  # noqa: F811
    g = gateway
    body, headers = event(g)
    g.enqueue(g.origin("github"), "github", "one", body, headers)
    g.w.store.set_meta(
        KEY, {"stopped_by": "bill", "reason": "drill", "scope": "factory"}
    )
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(202)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await g.deliver_pending(client)
        g.w.store.set_meta(KEY, None)
        g.w.store._db.execute("UPDATE factory_events SET next_attempt=0")
        await g.deliver_pending(client)
    assert requests == []
    assert rows(g)[0][9] is not None
    assert "stopped" in rows(g)[0][10]


@pytest.mark.asyncio
async def test_runtime_is_kept_in_line_with_workplace(tmp_path):
    w = _workplace(tmp_path, "http://runtime.invalid")
    raw = w.factory_config.raw()
    raw["factory"]["admin_auth_token"] = "control"
    w.factory_config.path.write_text(yaml.safe_dump(raw))
    runtime = {"stopped": None, "missing": False}
    calls = []

    def handler(request):
        assert request.headers["authorization"] == "Bearer control"
        calls.append(request.method)
        if runtime["missing"]:
            return httpx.Response(404)
        if request.method == "POST":
            runtime["stopped"] = json.loads(request.content)
        elif request.method == "DELETE":
            runtime["stopped"] = None
        return httpx.Response(200, json={"stopped": runtime["stopped"]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await w.kill_switch.sync(client)
        assert calls == ["GET"]
        w.store.set_meta(
            KEY,
            {
                "stopped_by": "bill",
                "reason": "drill",
                "scope": "factory",
                "by_installation_admin": True,
            },
        )
        await w.kill_switch.sync(client)
        assert runtime["stopped"] == {
            "stopped_by": "bill",
            "reason": "drill",
            "scope": "factory",
        }
        await w.kill_switch.sync(client)
        assert calls == ["GET", "GET", "POST", "GET"]
        w.store.set_meta(KEY, None)
        await w.kill_switch.sync(client)
        assert runtime["stopped"] is None
        # A runtime without the switch: Workplace still refuses work itself.
        runtime["missing"] = True
        w.store.set_meta(KEY, {"reason": "drill"})
        await w.kill_switch.sync(client)
    w.close()
