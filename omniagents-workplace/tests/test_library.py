from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from conftest import factory_env, signed_in
from omniagents_workplace.backup import backup, restore
from omniagents_workplace.library.state import Library, MAX_FILE
from omniagents_workplace.store import Store


def test_library_lifecycle_persistence_and_backup(tmp_path):
    data = tmp_path / "data"
    store = Store(data / "workplace.db")
    library = Library(store, data / "library")
    item = library.create("Event registration", "page", "owner")
    path = data / "library" / item["id"] / "index.html"
    path.write_text("<h1>Registration</h1>")
    assert library.read(item, item["main"]) == b"<h1>Registration</h1>"
    assert library.rename(item, "Event signup")["title"] == "Event signup"
    reopened = Library(store, data / "library")
    assert reopened.list()[0]["title"] == "Event signup"
    saved = backup(data, tmp_path / "backups")
    restore(saved, tmp_path / "restored")
    copy = Store(tmp_path / "restored/workplace.db")
    restored = Library(copy, tmp_path / "restored/library")
    assert (
        restored.read(restored.get(item["id"]), item["main"])
        == b"<h1>Registration</h1>"
    )
    restored.delete(restored.get(item["id"]))
    assert restored.list() == []
    assert not (restored.root / item["id"]).exists()
    copy.close()
    store.close()


def test_file_isolation(tmp_path):
    store = Store(tmp_path / "state.db")
    library = Library(store, tmp_path / "library")
    item = library.create("Test", "page", "owner")
    folder = library.root / item["id"]
    secret = tmp_path / "secret"
    secret.write_text("not an artifact")
    (folder / "link.txt").symlink_to(secret)
    (folder / "dir").symlink_to(tmp_path, target_is_directory=True)
    for path in [
        "../secret",
        str(secret),
        "link.txt",
        "dir/secret",
        ".hidden",
        "index.html/anything",
    ]:
        with pytest.raises(HTTPException):
            library.read(item, path)
    (folder / "large.txt").write_bytes(b"x" * (MAX_FILE + 1))
    with pytest.raises(HTTPException) as exc:
        library.read(item, "large.txt")
    assert exc.value.status_code == 413
    store.close()


def test_library_auth_preview_and_ownership(workplace_server):
    workplace, url = workplace_server
    with httpx.Client(base_url=url) as anonymous, signed_in(
        workplace, url
    ) as admin, signed_in(workplace, url, "priya") as member:
        assert anonymous.get("/workplace/api/library/artifacts").status_code == 401
        assert anonymous.get("/library/api/artifacts").status_code == 401
        made = admin.post(
            "/workplace/api/library/artifacts", json={"title": "Plan", "kind": "page"}
        )
        assert made.status_code == 200, made.text
        item = made.json()
        endpoint = f"/workplace/api/library/artifacts/{item['id']}"
        folder = workplace.library.root / item["id"]
        (folder / "index.html").write_text(
            '<h1>Actual preview</h1><script>document.body.dataset.ready="yes"</script>'
        )
        (folder / "data.json").write_text('{"count":3}')
        assert member.get(endpoint).status_code == 200
        assert member.patch(endpoint, json={"title": "Updated plan"}).status_code == 200
        assert member.delete(endpoint).status_code == 403
        page = anonymous.get(item["view"])
        assert page.status_code == 200 and "Actual preview" in page.text
        csp = page.headers["content-security-policy"]
        assert "sandbox allow-scripts;" in csp and "allow-same-origin" not in csp
        assert "connect-src" in csp and "form-action 'none'" in csp
        assert anonymous.get(
            item["view"].replace("/index.html", "/data.json")
        ).json() == {"count": 3}
        assert (
            anonymous.get(
                item["view"].replace(item["view"].split("/")[4], "wrong")
            ).status_code
            == 404
        )
        assert (
            admin.patch(
                endpoint,
                json={"title": "Forbidden"},
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert admin.delete(endpoint).status_code == 200
        assert anonymous.get(item["view"]).status_code == 404


def test_agent_tools_use_existing_token_and_revocation(workplace_server):
    workplace, url = workplace_server
    token = factory_env(url)["CANVAS_MCP_TOKEN"]
    assert factory_env(url)["LIBRARY_FILES_HOST_PATH"] == str(
        workplace.library.root.resolve()
    )
    with httpx.Client(
        base_url=url, headers={"Authorization": "Bearer " + token}
    ) as client:
        item = client.post(
            "/library/api/artifacts", json={"title": "Saved notes", "kind": "document"}
        ).json()
        assert item["folder"] == f"/workspace/Library/{item['id']}"
        assert (
            client.get("/library/api/artifacts/" + item["id"]).json()["markdown"]
            == "# Saved notes\n"
        )
        assert len(client.get("/library/api/artifacts").json()["artifacts"]) == 1
        workplace.canvas.update_member(
            workplace.canvas.member_for_token(token), token=None
        )
        assert client.get("/library/api/artifacts").status_code == 401
