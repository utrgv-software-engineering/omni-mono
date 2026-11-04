"""Deleting a factory removes it, and what it holds, at once; previews never see sign-in secrets."""

from omniagents_workplace.accounts import SESSION_COOKIE
from omniagents_workplace.deployments.gateway import (
    forwardable_cookie,
    forwardable_header,
)
from test_installation import installation, prefix  # noqa: F401 - fixture


def test_an_installation_administrator_deletes_a_factory_by_name(
    installation,
):  # noqa: F811
    i, client, owner, other, a, b = installation
    directory = i.directory / "factories" / b["id"]
    assert directory.is_dir()
    assert client.get(prefix(b) + "/workplace/api/session").status_code == 200
    url = f"/workplace/api/factories/{b['id']}"
    assert client.request("DELETE", url, json={"confirm": "wrong"}).status_code == 422
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(other))
    assert client.request("DELETE", url, json={"confirm": "Two"}).status_code == 403
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(owner))
    assert client.request("DELETE", url, json={"confirm": "Two"}).json() == {
        "deleted": b["id"]
    }
    assert b["id"] not in i.records and b["id"] not in i.instances
    assert not directory.exists()
    assert not [m for m in i.members.values() if m["factory_id"] == b["id"]]
    assert client.get(prefix(b) + "/workplace/api/session").status_code in (
        403,
        404,
        503,
    )
    ids = [f["id"] for f in client.get("/workplace/api/factories").json()["factories"]]
    assert ids == [a["id"]]
    deleted = i.audit_log.events(event="factory.deleted")[0]
    assert (deleted["actor"], deleted["name"]) == ("owner", "Two")
    # The other factory carries on.
    assert client.get(prefix(a) + "/workplace/api/session").status_code == 200


def test_previews_never_receive_sign_in_identities_or_secrets():
    for name in (
        "X-MS-CLIENT-PRINCIPAL",
        "x-ms-client-principal-name",
        "X-Workplace-Proxy-Token",
        "X-Forwarded-For",
        "Connection",
    ):
        assert not forwardable_header(name)
    assert forwardable_header("Accept") and forwardable_header("Authorization")
    for name in (
        "AppServiceAuthSession",
        "AppServiceAuthSession1",
        "workplace_session",
    ):
        assert not forwardable_cookie(name)
    assert forwardable_cookie("app_session")
