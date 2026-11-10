from urllib.parse import parse_qs, urlsplit

from omniagents_workplace.accounts import SESSION_COOKIE
from omniagents_workplace.deployments.gateway import COOKIE
from test_installation import (
    installation as installation,
)  # noqa: PLC0414 - pytest fixture re-export


def grant(i, client, record):
    i.deployments.configure(record["id"], True)
    identity = i.deployments.identity
    token = identity.issue(
        "access",
        {"factory": record["id"], "login": client.cookies.get(SESSION_COOKIE)},
        300,
    )
    client.cookies.set(COOKIE, token)
    return i.deployments.url(record["id"])


def authorize(i, client, record, base):
    callback = base + "/auth/authentik/callback"
    r = client.get(
        base + "/workplace-auth/application/o/authorize/",
        params={
            "client_id": record["id"],
            "response_type": "code",
            "redirect_uri": callback,
            "state": "test-state",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    return {
        "client_id": record["id"],
        "client_secret": i.records[record["id"]]["deployment"]["secret"],
        "grant_type": "authorization_code",
        "redirect_uri": callback,
        "code": parse_qs(urlsplit(r.headers["location"]).query)["code"][0],
    }


def test_code_is_single_use_and_bound_to_factory(installation):
    i, client, owner, other, a, b = installation
    base = grant(i, client, a)
    form = authorize(i, client, a, base)
    token_url = base + "/workplace-auth/application/o/token/"
    other_base = i.deployments.url(b["id"])
    i.deployments.configure(b["id"], True)
    assert (
        client.post(
            other_base + "/workplace-auth/application/o/token/", data=form
        ).status_code
        == 401
    )
    r = client.post(token_url, data=form)
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    assert client.post(token_url, data=form).status_code == 403
    info = client.get(
        base + "/workplace-auth/application/o/userinfo/",
        headers={"Authorization": "Bearer " + token},
    )
    assert info.status_code == 200
    assert info.json()["sub"] == owner.id
    assert (
        client.get(
            other_base + "/workplace-auth/application/o/userinfo/",
            headers={"Authorization": "Bearer " + token},
        ).status_code
        == 403
    )
    i.grant(a["id"], other.id, "admin")
    i.grant(a["id"], owner.id, "member")
    assert (
        client.get(
            base + "/workplace-auth/application/o/userinfo/",
            headers={"Authorization": "Bearer " + token},
        ).status_code
        == 403
    )


def test_redirect_and_workplace_session_are_enforced(installation):
    i, client, _owner, _other, a, _b = installation
    base = grant(i, client, a)
    r = client.get(
        base + "/workplace-auth/application/o/authorize/",
        params={
            "client_id": a["id"],
            "response_type": "code",
            "redirect_uri": "https://evil.test/callback",
            "state": "x",
        },
        follow_redirects=False,
    )
    assert r.status_code == 400
    form = authorize(i, client, a, base)
    i.accounts.sign_out(client.cookies.get(SESSION_COOKIE))
    assert (
        client.post(
            base + "/workplace-auth/application/o/token/", data=form
        ).status_code
        == 403
    )
    assert client.get(base + "/", follow_redirects=False).status_code == 403


def test_deployment_configuration_is_factory_scoped(installation):
    i, client, _owner, other, a, b = installation
    i.deployments.configure(a["id"], True)
    i.deployments.configure(b["id"], True)
    first, second = (i.records[r["id"]]["deployment"] for r in (a, b))
    assert first["secret"] != second["secret"]
    assert abs(first["port"] - second["port"]) >= 3
    assert i.deployments.directory(a["id"]) != i.deployments.directory(b["id"])
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(other))
    assert (
        client.post(
            "/workplace/api/factories/" + a["id"] + "/coolify/start"
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/workplace/api/factories/" + b["id"] + "/coolify/start"
        ).status_code
        == 403
    )


def test_certificate_permission_requires_enabled_exact_factory_host(installation):
    i, client, _owner, _other, a, _b = installation
    host = urlsplit(i.deployments.url(a["id"])).hostname
    endpoint = "/workplace/deployments/allow-host"
    assert client.get(endpoint, params={"domain": host}).status_code == 403
    i.deployments.configure(a["id"], True)
    client.cookies.clear()
    assert client.get(endpoint, params={"domain": host}).status_code == 200
    assert (
        client.get(endpoint, params={"domain": host + ".evil.test"}).status_code == 403
    )
    assert client.get(endpoint, params={"domain": "evil.test"}).status_code == 403
    i.deployments.configure(a["id"], False)
    assert client.get(endpoint, params={"domain": host}).status_code == 403


def test_native_oauth_redirect_rewrites_case_insensitive_location(
    installation, monkeypatch
):
    import httpx
    from omniagents_workplace.deployments import gateway

    i, client, _owner, _other, a, _b = installation
    base = grant(i, client, a)
    i.deployments.runners[a["id"]] = object()
    original = httpx.AsyncClient

    def upstream(request):
        return httpx.Response(
            302,
            headers=[
                (
                    "Location",
                    i.deployments.backchannel(a["id"])
                    + "/workplace-auth/application/o/authorize/?state=one",
                ),
                ("Set-Cookie", "first=1"),
                ("Set-Cookie", "second=2"),
            ],
            stream=httpx.ByteStream(b""),
        )

    monkeypatch.setattr(
        gateway.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(upstream), **kwargs),
    )
    response = client.get(base + "/auth/authentik/redirect", follow_redirects=False)
    assert response.status_code == 302
    assert (
        response.headers["location"]
        == base + "/workplace-auth/application/o/authorize/?state=one"
    )
    assert len(response.headers.get_list("set-cookie")) == 2


def test_preview_routes_are_registered_scoped_and_strip_parent_cookies(
    installation, monkeypatch
):
    import httpx
    from omniagents_workplace.deployments import gateway

    i, client, _owner, _other, a, _b = installation
    i.deployments.configure(a["id"], True)
    url = i.deployments.preview_url("a" * 32)
    host = urlsplit(url).netloc
    record = {"factory": a["id"], "state": "queued", "hosts": [host]}
    i.store.put("deployment.preview", "owned", record)
    original = httpx.AsyncClient

    def upstream(request):
        assert request.headers["host"] == host
        assert "workplace_session" not in request.headers.get("cookie", "")
        return httpx.Response(
            200,
            headers=[
                ("Set-Cookie", "app=value; Domain=testserver; Path=/"),
                ("Set-Cookie", "workplace_session=bad; Domain=testserver"),
            ],
            stream=httpx.ByteStream(b"preview"),
        )

    monkeypatch.setattr(
        gateway.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(upstream), **kwargs),
    )
    response = client.get(url)
    assert response.status_code == 200 and response.text == "preview"
    assert response.headers.get_list("set-cookie") == ["app=value; Path=/"]
    assert (
        client.get(
            "/workplace/deployments/allow-host", params={"domain": host}
        ).status_code
        == 200
    )
    assert client.get(i.deployments.preview_url("b" * 32)).status_code == 404
    i.store.put("deployment.preview", "owned", {**record, "state": "closing"})
    assert client.get(url).status_code == 404
    assert (
        client.get(
            "/workplace/deployments/allow-host", params={"domain": host}
        ).status_code
        == 403
    )
