"""Editing the factory from the Workplace: each save a commit, published to the running factory."""

import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from omniagents_workplace.factory_definition import (
    Change,
    FactoryDefinition,
    describe,
)
from omniagents_workplace.server import Workplace, load_seed

INSTRUCTIONS = "agents/foreman/instructions.md"


class FakeFactory:
    """The factory's definition routes: what it was asked, and what it answers."""

    def __init__(self):
        self.calls = []
        self.uncommitted = []
        self.problems = {}
        self.advance = (200, None)

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/integrations/definition":
            return 200, {
                "agents": [],
                "uncommitted": self.uncommitted,
                "commit": "c" * 40,
            }
        if path == "/integrations/definition/check":
            return 200, {"problems": self.problems}
        status, answer = self.advance
        return status, answer or {"commit": body["commit"]}

    def advanced(self):
        return [b["commit"] for m, p, b in self.calls if p.endswith("/advance")]


@pytest.fixture
def editor(tmp_path):
    w = Workplace(tmp_path / "data", base_url="http://w", seed=load_seed())
    repo = w.github.create_repo(
        "acme", "factory", "admin", auto_init=True, protect_default_branch=True
    )
    w.github.write_files(
        repo,
        "admin",
        "main",
        [(INSTRUCTIONS, b"Before.\n")],
        "Start",
        bypass_protection=True,
    )
    definition = FactoryDefinition(w, lambda: "acme/factory", lambda: ("http://f", "t"))
    fake = FakeFactory()
    definition.factory = fake
    yield SimpleNamespace(
        w=w,
        repo=repo,
        definition=definition,
        fake=fake,
        admin=SimpleNamespace(github_login="admin"),
    )
    w.close()


def _main(e):
    return e.repo.bare.ref_sha("main")


def test_saving_commits_on_main_as_the_person_and_publishes(editor):
    e = editor
    before = _main(e)
    result = e.definition.save(
        e.admin, before, [Change(path=INSTRUCTIONS, content="After.\n")], None
    )
    commit = e.repo.bare.commit(_main(e))
    assert result == {"commit": commit.sha, "published": True, "detail": None}
    assert commit.parents == [before]
    assert commit.subject == "Edit foreman instructions"
    assert commit.author_name == "Acme Admin"
    assert e.repo.bare.blob("main", INSTRUCTIONS)[1] == b"After.\n"
    assert commit.sha in e.fake.advanced()
    assert e.definition.read(INSTRUCTIONS)["content"] == "After.\n"


def test_requiring_review_opens_a_pull_request_instead(editor):
    e = editor
    e.w.store.set_meta("factory.definition.require_review", True)
    before = _main(e)
    result = e.definition.save(
        e.admin, before, [Change(path=INSTRUCTIONS, content="After.\n")], None
    )
    assert _main(e) == before
    assert result["published"] is False
    number = int(result["pull_request"].rsplit("/", 1)[1])
    pull = e.repo.issues[number]
    assert pull.title == "Edit foreman instructions" and pull.pull.base == "main"
    assert e.fake.advanced() == []


def test_the_factory_says_what_is_wrong(editor):
    e = editor
    e.fake.problems = {INSTRUCTIONS: ["Instructions are not a valid template"]}
    with pytest.raises(HTTPException) as refused:
        e.definition.save(
            e.admin, _main(e), [Change(path=INSTRUCTIONS, content="{% if %}")], None
        )
    assert refused.value.status_code == 422
    assert refused.value.detail == {"problems": e.fake.problems}
    assert e.repo.bare.blob("main", INSTRUCTIONS)[1] == b"Before.\n"


def test_a_save_over_someone_elses_change_is_refused(editor):
    e = editor
    opened = _main(e)
    e.definition.save(
        e.admin, opened, [Change(path=INSTRUCTIONS, content="Theirs.\n")], None
    )
    with pytest.raises(HTTPException) as refused:
        e.definition.save(
            e.admin, opened, [Change(path=INSTRUCTIONS, content="Mine.\n")], None
        )
    assert refused.value.status_code == 409
    # Another file: saved on top of their change.
    e.definition.save(
        e.admin,
        opened,
        [
            Change(
                path="automations/nightly/automation.md",
                content="---\ntriggers: []\n---\nGo.\n",
            )
        ],
        None,
    )
    assert e.repo.bare.blob("main", INSTRUCTIONS)[1] == b"Theirs.\n"
    assert e.repo.bare.commit(_main(e)).subject == "Add automation nightly"


def test_saving_waits_for_changes_made_on_the_server(editor):
    e = editor
    e.fake.uncommitted = ["agents/foreman/instructions.md"]
    with pytest.raises(HTTPException) as refused:
        e.definition.save(
            e.admin, _main(e), [Change(path=INSTRUCTIONS, content="After.\n")], None
        )
    assert refused.value.status_code == 409
    assert refused.value.detail["paths"] == ["agents/foreman/instructions.md"]


def test_a_change_that_needs_a_release_is_saved_and_says_so(editor):
    e = editor
    e.fake.advance = (422, {"detail": "only with a release", "paths": ["project.yml"]})
    result = e.definition.save(
        e.admin, _main(e), [Change(path=INSTRUCTIONS, content="After.\n")], None
    )
    assert result["published"] is False
    assert "next release" in result["detail"] and "project.yml" in result["detail"]
    assert e.definition.tree()["published"]["ok"] is False


def test_history_and_restore(editor):
    e = editor
    first = _main(e)
    e.definition.save(
        e.admin, first, [Change(path=INSTRUCTIONS, content="After.\n")], None
    )
    history = e.definition.history(INSTRUCTIONS)["commits"]
    assert [c["message"] for c in history] == ["Edit foreman instructions", "Start"]
    assert "+After." in e.definition.diff(INSTRUCTIONS, history[0]["sha"])["diff"]
    restored = e.definition.restore(e.admin, INSTRUCTIONS, first)
    assert restored["published"] is True
    assert e.repo.bare.blob("main", INSTRUCTIONS)[1] == b"Before.\n"
    assert e.repo.bare.commit(_main(e)).subject == f"Restore foreman as of {first[:7]}"


def test_main_moving_is_offered_to_the_factory(editor):
    e = editor
    e.w.github.subscribe(e.definition.on_github)
    e.w.github.create_branch(e.repo, "admin", "change", "main")
    e.w.github.write_files(
        e.repo, "admin", "change", [(INSTRUCTIONS, b"Merged.\n")], "Change"
    )
    assert e.fake.advanced() == []  # a branch is not main
    pull = e.w.github.create_pull(e.repo, "admin", "Change", "change", "main", "")
    merged = e.w.github.merge_pull(e.repo, pull, "admin", bypass_protection=True)
    for _ in range(50):
        if merged in e.fake.advanced():
            break
        time.sleep(0.05)
    assert merged in e.fake.advanced()


def test_editing_needs_the_repository_here(editor):
    e = editor
    e.w.repository_settings.activate(
        {
            "provider": "github",
            "repositories": [],
            "factory_repository": None,
            "self_improvement": False,
        }
    )
    with pytest.raises(HTTPException) as refused:
        e.definition.tree()
    assert refused.value.status_code == 409


def test_commit_messages_say_what_changed():
    existed = {
        "a/foreman/instructions.md": True,
        "skills/notes/SKILL.md": False,
        "automations/x/automation.md": True,
    }
    assert describe([("skills/notes/SKILL.md", b"x")], existed) == "Add skill notes"
    assert (
        describe([("automations/x/automation.md", None)], existed)
        == "Delete automation x"
    )
    assert (
        describe(
            [("a/foreman/instructions.md", b"x"), ("skills/notes/SKILL.md", b"x")],
            existed,
        )
        == "Edit foreman instructions and 1 more"
    )
