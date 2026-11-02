from urllib.parse import urlsplit, parse_qs

import httpx
import pytest

from conftest import signed_in, factory_env
from omniagents_workplace.sharing import Sharing


@pytest.mark.parametrize("kind", ["artifact", "canvas"])
def test_guest_review_is_scoped_named_durable_and_revocable(
    workplace_server, kind, monkeypatch
):
    w, url = workplace_server

    def no_wakeup(*args, **kwargs):
        raise AssertionError("Comments must not enqueue agent events")

    monkeypatch.setattr(w.event_gateway, "enqueue", no_wakeup)
    with signed_in(w, url) as admin, signed_in(w, url, "priya") as member, httpx.Client(
        base_url=url
    ) as guest:
        if kind == "artifact":
            item = admin.post(
                "/workplace/api/library/artifacts",
                json={"title": "Review", "kind": "page"},
            ).json()
            (w.library.root / item["id"] / "index.html").write_text(
                "<h1>Shared design</h1>"
            )
        else:
            item = admin.post(
                "/workplace/api/canvas/canvases", json={"title": "Review"}
            ).json()
        path = f"/workplace/api/sharing/{kind}/{item['id']}"
        assert member.post(path).status_code == 403
        made = admin.post(path)
        assert made.status_code == 200, made.text
        link = made.json()
        parts = urlsplit(link["url"])
        token = parse_qs(parts.fragment)["token"][0]
        route = parts.path
        assert guest.get(route + "/api").status_code == 404
        headers = {"Authorization": "Bearer " + token}
        r = guest.get(route + "/api", headers=headers)
        assert r.status_code == 200, r.text
        assert "noindex" in r.headers["x-robots-tag"]
        detail = r.json()["item"]
        assert "folder" not in detail and not detail.get("versions")
        assert (
            guest.post(
                route + "/comments", headers=headers, json={"body": "Feedback"}
            ).status_code
            == 401
        )
        identity = guest.post(
            route + "/identity", headers=headers, json={"name": "Stakeholder"}
        )
        assert identity.status_code == 200, identity.text
        assert "HttpOnly" in identity.headers["set-cookie"]
        note = guest.post(
            route + "/comments", headers=headers, json={"body": "Please clarify this"}
        )
        assert note.status_code == 200, note.text
        assert note.json()["author"] == "Stakeholder" and note.json()["guest"]
        assert len(Sharing(w).comments(kind, item["id"])) == 1
        asset = detail["view"] + ("canvas.json" if kind == "canvas" else "")
        assert guest.get(asset).status_code == 200
        if kind == "artifact":
            env = factory_env(url)
            r = guest.get(
                f"/library/api/artifacts/{item['id']}/comments",
                headers={"Authorization": "Bearer " + env["CANVAS_MCP_TOKEN"]},
            )
            assert (
                r.status_code == 200
                and r.json()["comments"][0]["body"] == "Please clarify this"
            )
        assert admin.delete(path + "/" + link["id"]).status_code == 200
        assert guest.get(route + "/api", headers=headers).status_code == 404
        assert guest.get(asset).status_code == 404
        assert (
            guest.post(
                route + "/comments", headers=headers, json={"body": "After revoke"}
            ).status_code
            == 404
        )
        assert len(Sharing(w).comments(kind, item["id"])) == 1


def test_crawler_headers_include_auth_errors(workplace_server):
    _, url = workplace_server
    with httpx.Client(base_url=url) as client:
        robots = client.get("/robots.txt")
        assert robots.text == "User-agent: *\nDisallow: /\n"
        for path in [
            "/robots.txt",
            "/workplace/api/library/artifacts",
            "/share/missing/api",
        ]:
            r = client.get(path)
            assert "noindex" in r.headers["x-robots-tag"]
            assert r.headers["referrer-policy"] == "no-referrer"
