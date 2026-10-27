from fastapi.testclient import TestClient

from omniagents_workplace.server import Workplace, create_app, load_seed


def test_sandboxes_reach_git_and_uploads_only(tmp_path):
    """On the sandbox-facing address (TestClient's is ``testserver``), the
    UI's API, which acts as whoever it is told, and the MCP servers are off
    limits; git and the upload URLs are not."""
    workplace = Workplace(
        tmp_path / "data", base_url="http://testserver", seed=load_seed()
    )
    app = create_app(workplace, static_dir=tmp_path, sandbox_addresses=["testserver"])
    with TestClient(app) as client:
        refs = client.get(
            "/github/acme/api-service.git/info/refs?service=git-upload-pack"
        )
        assert refs.status_code == 200
        assert client.post("/slack/upload/nope", content=b"x").status_code != 403
        assert (
            client.put("/linear/uploads/put/nope", content=b"x").status_code == 403
        )  # an unknown slot, not the boundary
        for method, path in [
            ("post", "/workplace/api/linear/issues/ENG-1/comments"),
            ("get", "/workplace/api/state"),
            ("get", "/workplace/api/connect"),
            ("post", "/linear/mcp"),
            ("post", "/github/mcp"),
            ("post", "/slack/mcp"),
            ("post", "/linear/graphql"),
            ("get", "/"),
        ]:
            response = getattr(client, method)(path)
            assert response.status_code == 403 and response.text.startswith(
                "Sandboxes reach"
            ), path

    local = Workplace(
        tmp_path / "local", base_url="http://testserver", seed=load_seed()
    )
    with TestClient(create_app(local, static_dir=tmp_path)) as client:
        # Here the UI's API answers, for people who sign in.
        assert client.get("/workplace/api/state").status_code == 401
        assert client.get("/workplace/api/session").status_code == 200


def test_the_ui_page_is_revalidated_and_its_assets_kept(tmp_path):
    """An upgraded workplace's UI is the one browsers load: the page is never
    used unchecked from a cache, and its content-named assets are kept."""
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<script src=/assets/index-abc.js></script>")
    (static / "assets" / "index-abc.js").write_text("1")
    (static / "favicon.svg").write_text("<svg/>")
    workplace = Workplace(
        tmp_path / "data", base_url="http://testserver", seed=load_seed()
    )
    with TestClient(create_app(workplace, static_dir=static)) as client:
        for path in ("/", "/factory/runs/1"):
            assert client.get(path).headers["cache-control"] == "no-cache", path
        assert (
            "immutable" in client.get("/assets/index-abc.js").headers["cache-control"]
        )
        assert client.get("/favicon.svg").headers["cache-control"] == "no-cache"
