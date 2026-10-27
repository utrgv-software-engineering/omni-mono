import time

from omniagents_workplace.server import Workplace, load_seed


def _wait(condition, seconds=10.0):
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.05)


def test_a_restarted_workplace_carries_on(tmp_path):
    data = tmp_path / "data"
    first = Workplace(data, base_url="http://w", seed=load_seed())
    ws, gh, li = first.slack, first.github, first.linear

    general = ws.channel_by_name("general")
    posted = ws.post_message("U0ACME0PRI", general.id, "Standup in 5")
    ws.add_reaction("U0ACME0MAR", general.id, posted.ts, "eyes")
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(64))
    upload = ws.start_upload("U0ACME0PRI", "page.png", len(png))
    ws.receive_upload(upload.upload_token, png)
    ws.complete_upload(
        "U0ACME0PRI", upload.id, channel_id=general.id, initial_comment="Before"
    )
    channel = ws.create_channel("U0ACME0PRI", "launch")

    repo = gh.repos["acme/api-service"]
    issue = gh.create_issue(repo, "priya", "Cap per_page", "At 100.")
    gh.add_comment(repo, issue, "marcus", "Agreed.")
    gh.create_branch(repo, "priya", "priya/cap", None)
    gh.write_files(repo, "priya", "priya/cap", [("CAP.md", b"100\n")], "Cap at 100")
    pull = gh.create_pull(repo, "priya", "Cap per_page at 100", "priya/cap", "main")
    [run] = gh.runs_for(repo, gh.head_sha(repo, pull))
    # Its tests workflow runs on this machine.
    assert run.path == ".github/workflows/tests.yml"

    team = li.find_team("ENG")
    priya = li.find_user("priya")
    ticket = li.create_issue(team.id, priya.id, "Slow tasks API", "Big teams wait.")
    li.add_comment(ticket.id, priya.id, "Seen on the Acme board too.")
    li.update_issue(ticket, priya.id, delegate=li.find_user("acme-factory").id)
    [session] = [s for s in li.sessions.values() if s.issue_id == ticket.id]
    first.close()

    # A seed is for a new workplace: one with a workplace in it carries on.
    again = Workplace(
        data, base_url="http://w", seed={"slack": {"team": {"name": "Other"}}}
    )
    try:
        ws, gh, li = again.slack, again.github, again.linear
        assert ws.name == "Acme"
        assert ws.message(general.id, posted.ts).reactions == {"eyes": ["U0ACME0MAR"]}
        assert ws.files[upload.id].content == png and ws.files[upload.id].completed
        assert ws.channel_by_name("launch").id == channel.id
        # New ids and times follow the old ones.
        later = ws.post_message("U0ACME0PRI", general.id, "Moved to 10")
        assert float(later.ts) > float(posted.ts)
        assert ws.create_channel("U0ACME0PRI", "retro").id != channel.id

        repo = gh.repos["acme/api-service"]
        assert [c.body for c in repo.issues[issue.number].comments] == ["Agreed."]
        assert repo.bare.blob("priya/cap", "CAP.md")[1] == b"100\n"
        nxt = gh.create_issue(repo, "priya", "Next", "")
        assert nxt.number == pull.number + 1 and nxt.id > max(
            c.id for c in repo.issues[issue.number].comments
        )
        # The run is kept: finished, or cancelled if closing the workplace stopped it.
        restored = gh.run(repo, run.id)
        assert restored.status == "completed" and restored.conclusion in (
            "success",
            "cancelled",
        )

        assert (
            li.find_issue(ticket.identifier).delegate_id
            == li.find_user("acme-factory").id
        )
        assert li.sessions[session.id].comment_id in li.comments
        assert (
            li.create_issue(team.id, priya.id, "After", "").number == ticket.number + 1
        )
    finally:
        again.close()


def test_a_new_workplace_without_a_seed_is_empty(tmp_path):
    workplace = Workplace(tmp_path / "data", base_url="http://w")
    try:
        assert not workplace.slack.users and not workplace.slack.channels
        assert not workplace.github.repos and not workplace.github.users
        assert not workplace.linear.users and not workplace.linear.teams
    finally:
        workplace.close()
