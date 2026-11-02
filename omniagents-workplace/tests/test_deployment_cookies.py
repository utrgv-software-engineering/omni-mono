from omniagents_workplace.deployments.cookies import HostCookies
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.testclient import TestClient


async def echo(scope, receive, send):
    request = Request(scope, receive)
    response = JSONResponse({"session": request.cookies.get("workplace_session")})
    response.set_cookie("workplace_session", "new", httponly=True)
    await response(scope, receive, send)


def test_https_rejects_parent_domain_cookie_and_uses_host_prefix():
    client = TestClient(
        HostCookies(echo, "https://workplace.example.test"),
        base_url="https://workplace.example.test",
    )
    response = client.get("/", headers={"Cookie": "workplace_session=attacker"})
    assert response.json()["session"] is None
    value = response.headers["set-cookie"]
    assert value.startswith("__Host-workplace_session=")
    assert "Secure" in value and "HttpOnly" in value and "Path=/" in value
    assert "Domain=" not in value
    response = client.get(
        "/",
        headers={
            "Cookie": "workplace_session=attacker; __Host-workplace_session=legitimate"
        },
    )
    assert response.json()["session"] == "legitimate"


def test_local_http_login_is_unchanged():
    client = TestClient(HostCookies(echo, "http://localhost"))
    response = client.get("/", headers={"Cookie": "workplace_session=local"})
    assert response.json()["session"] == "local"
    assert response.headers["set-cookie"].startswith("workplace_session=")
