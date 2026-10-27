from fastapi.testclient import TestClient

from omniagents_workplace.accounts import SESSION_COOKIE
from omniagents_workplace.server import Workplace, create_app, load_seed


def _app(workplace, tmp_path):
    return TestClient(create_app(workplace, static_dir=tmp_path))


SETUP = {
    "organization": "Initech",
    "slug": "initech",
    "username": "bill",
    "name": "Bill Lumbergh",
    "email": "bill@initech.test",
    "password": "tps-reports",
    "tz": "America/New_York",
}


def test_a_new_workplace_is_set_up_by_whoever_has_its_setup_link(tmp_path):
    workplace = Workplace(tmp_path / "data", base_url="http://testserver")
    with _app(workplace, tmp_path) as client:
        assert client.get("/workplace/api/session").json() == {
            "account": None,
            "setup": True,
            "organization": None,
            "setup_here": False,
            "environment": "development",
            "claim_here": None,
            "configurable": False,
            "factory": False,
            "architecture": "specialist",
        }
        assert client.get("/workplace/api/state").status_code == 401
        assert (
            client.post(
                "/workplace/api/setup", json={**SETUP, "token": "guess"}
            ).status_code
            == 403
        )
        done = client.post(
            "/workplace/api/setup", json={**SETUP, "token": workplace.setup_token}
        )
        assert done.status_code == 200 and done.json()["account"]["admin"] is True
        state = client.get("/workplace/api/state").json()
        assert state["me"]["username"] == "bill"
        assert state["slack"]["team"]["name"] == "Initech"
        assert workplace.slack.tz == "America/New_York"
        assert (
            workplace.slack.tz_of(workplace.slack.users[state["me"]["slack_id"]])
            == "America/New_York"
        )
        [general] = state["slack"]["channels"]
        assert (
            general["name"] == "general"
            and general["is_general"]
            and general["members"] == [state["me"]["slack_id"]]
        )
        assert workplace.github.users["initech"].type == "Organization"
        assert (
            workplace.linear.org_name == "Initech"
            and workplace.linear.find_user("bill").is_admin
        )
        # Set up once.
        again = client.post(
            "/workplace/api/setup",
            json={**SETUP, "username": "other", "token": workplace.setup_token},
        )
        assert again.status_code == 409
    workplace.close()


def test_people_join_by_invite_and_act_as_themselves(tmp_path):
    workplace = Workplace(tmp_path / "data", base_url="http://testserver")
    app = create_app(workplace, static_dir=tmp_path)
    with TestClient(app) as admin:
        client = TestClient(app)  # the same app, for a second person
        admin.post(
            "/workplace/api/setup", json={**SETUP, "token": workplace.setup_token}
        )
        added = admin.post(
            "/workplace/api/people",
            json={
                "username": "milton",
                "name": "Milton Waddams",
                "email": "milton@initech.test",
            },
        ).json()
        token = added["invite_url"].rsplit("/", 1)[1]
        assert added["invite_url"] == f"http://testserver/invite/{token}"
        assert (
            admin.post(
                "/workplace/api/people",
                json={"username": "milton", "name": "M", "email": "m@x"},
            ).status_code
            == 422
        )

        assert (
            client.get(f"/workplace/api/invites/{token}").json()["username"] == "milton"
        )
        assert (
            client.post(
                f"/workplace/api/invites/{token}", json={"password": "short"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/workplace/api/invites/{token}", json={"password": "my-stapler"}
            ).status_code
            == 200
        )
        # An invite works once.
        assert client.get(f"/workplace/api/invites/{token}").status_code == 404

        me = client.get("/workplace/api/state").json()["me"]
        general = workplace.slack.general()
        posted = client.post(
            "/workplace/api/slack/messages",
            json={"channel": general.id, "text": "Have you seen my stapler?"},
        )
        assert posted.json()["message"]["user"] == me["slack_id"]
        # What someone else's private channel holds is theirs.
        private = workplace.slack.create_channel(
            workplace.accounts.by_username("bill").slack_id, "bosses", is_private=True
        )
        assert private.id not in [
            c["id"]
            for c in client.get("/workplace/api/state").json()["slack"]["channels"]
        ]
        assert (
            client.get(
                f"/workplace/api/slack/channels/{private.id}/messages"
            ).status_code
            == 404
        )

        # Only an admin adds people; changes are JSON.
        assert (
            client.post(
                "/workplace/api/people",
                json={"username": "x", "name": "X", "email": "x@x"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/workplace/api/slack/messages",
                data={"channel": general.id, "text": "hi"},
            ).status_code
            == 415
        )

        client.post("/workplace/api/signout")
        assert client.get("/workplace/api/state").status_code == 401
        assert (
            client.post(
                "/workplace/api/signin",
                json={"username": "milton", "password": "wrong-one"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/workplace/api/signin",
                json={"username": "milton", "password": "my-stapler"},
            ).status_code
            == 200
        )

        # A disabled person is signed out and cannot sign in.
        admin.patch(f"/workplace/api/people/{me['id']}", json={"disabled": True})
        assert client.get("/workplace/api/state").status_code == 401
        assert (
            client.post(
                "/workplace/api/signin",
                json={"username": "milton", "password": "my-stapler"},
            ).status_code
            == 401
        )
        assert workplace.slack.users[me["slack_id"]].deleted
    workplace.close()


def test_a_sign_in_outlasts_a_restart(tmp_path):
    workplace = Workplace(tmp_path / "data", base_url="http://testserver")
    with _app(workplace, tmp_path) as client:
        client.post(
            "/workplace/api/setup", json={**SETUP, "token": workplace.setup_token}
        )
        cookie = client.cookies[SESSION_COOKIE]
    workplace.close()
    again = Workplace(tmp_path / "data", base_url="http://testserver")
    assert again.accounts.signed_in(cookie).username == "bill"
    again.close()


def test_a_seeds_people_are_accounts_with_its_passwords(tmp_path):
    workplace = Workplace(
        tmp_path / "data", base_url="http://testserver", seed=load_seed()
    )
    people = {a.username: a for a in workplace.accounts.people()}
    assert set(people) == {"admin", "priya", "marcus", "dana"}
    assert people["admin"].admin and not people["priya"].admin
    assert not workplace.accounts.needs_setup()
    # Fixture people sign in with the seed's passwords.
    assert workplace.accounts.sign_in("admin", "acme-admin")[0].admin
    assert workplace.accounts.sign_in("priya", "acme-priya")[0].username == "priya"
    workplace.close()


def test_a_new_workplace_fills_up_from_its_ui(tmp_path):
    import base64
    import os
    import subprocess

    workplace = Workplace(tmp_path / "data", base_url="http://testserver")
    with _app(workplace, tmp_path) as client:
        client.post(
            "/workplace/api/setup", json={**SETUP, "token": workplace.setup_token}
        )
        made = client.post(
            "/workplace/api/github/repos",
            json={
                "name": "tps",
                "description": "Cover sheets",
                "protect_default_branch": True,
            },
        )
        assert made.status_code == 200, made.text
        assert made.json() == {
            "full_name": "initech/tps",
            "clone_url": "http://testserver/github/initech/tps.git",
        }
        assert (
            client.post("/workplace/api/github/repos", json={"name": "tps"}).status_code
            == 422
        )
        repo = workplace.github.repos["initech/tps"]
        assert repo.bare.blob("main", "README.md")[1] == b"# tps\n\nCover sheets\n"
        assert repo.protected == ["main"]

        team = client.post(
            "/workplace/api/linear/teams", json={"key": "tps", "name": "TPS"}
        ).json()
        assert team["key"] == "TPS"
        issue = client.post(
            "/workplace/api/linear/issues",
            json={"team": "TPS", "title": "New cover sheets"},
        ).json()
        assert issue["identifier"] == "TPS-1"

        channel = client.post(
            "/workplace/api/slack/channels", json={"name": "tps-reports"}
        ).json()["channel"]
        assert channel["name"] == "tps-reports"

        # Its people push with their own tokens.
        token = client.post("/workplace/api/me/tokens/github").json()["token"]
    workplace.close()

    # The same, over a real server: a person's token pushes a branch.
    import threading
    import time

    import uvicorn

    from conftest import _free_port

    again = Workplace(tmp_path / "data", base_url="http://127.0.0.1")
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    again.base_url = base
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(again, static_dir=tmp_path), port=port, log_level="warning"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    try:
        env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "Bill",
            "GIT_AUTHOR_EMAIL": "bill@initech.test",
            "GIT_COMMITTER_NAME": "Bill",
            "GIT_COMMITTER_EMAIL": "bill@initech.test",
        }
        basic = base64.b64encode(f"bill:{token}".encode()).decode()
        auth = ["-c", f"http.extraHeader=Authorization: Basic {basic}"]

        def git(*args, cwd=tmp_path):
            return subprocess.run(
                ["git", *auth, *args],
                cwd=cwd,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

        git("clone", f"{base}/github/initech/tps.git", "tps")
        (tmp_path / "tps" / "SHEET.md").write_text("cover\n")
        git("checkout", "-b", "bill/sheet", cwd=tmp_path / "tps")
        git("add", "SHEET.md", cwd=tmp_path / "tps")
        git("commit", "-m", "Add the cover sheet", cwd=tmp_path / "tps")
        git("push", "origin", "bill/sheet", cwd=tmp_path / "tps")
        assert again.github.repos["initech/tps"].bare.ref_sha("bill/sheet")
        # The push is Bill's.
        [push] = [
            e
            for e in again.wire.recent(20)
            if e.kind == "git" and e.title == "git push"
        ]
        assert push.request["pusher"] == "bill" and push.status == "ok"
    finally:
        server.should_exit = True
        thread.join(5)
        again.close()


def test_on_its_own_machine_a_new_workplace_is_set_up_without_the_link(tmp_path):
    workplace = Workplace(tmp_path / "data", base_url="http://127.0.0.1:7300")
    app = create_app(workplace, static_dir=tmp_path)
    with TestClient(app, client=("127.0.0.1", 50000)) as here:
        assert here.get("/workplace/api/session").json()["setup_here"] is True
        # Behind a proxy, "local" is the proxy: the link is needed.
        proxied = here.post(
            "/workplace/api/setup",
            json=SETUP,
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
        assert proxied.status_code == 403
        assert here.post("/workplace/api/setup", json=SETUP).status_code == 200
        assert here.get("/workplace/api/session").json()["setup_here"] is False
    workplace.close()
