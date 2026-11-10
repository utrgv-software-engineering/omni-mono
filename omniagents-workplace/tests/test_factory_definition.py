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


def test_editing_works_while_github_serves_the_product_repositories(editor):
    # The factory runs from its own repository here; connecting GitHub for
    # the product repositories doesn't move it.
    e = editor
    e.w.repository_settings.activate(
        {
            "provider": "github",
            "repositories": ["acme/webapp"],
            "factory_repository": None,
            "self_improvement": False,
        }
    )
    assert e.definition.tree()["repository"] == "acme/factory"
    result = e.definition.save(
        e.admin, _main(e), [Change(path=INSTRUCTIONS, content="After.\n")], None
    )
    assert result["published"] is True


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


def test_a_hosted_factory_is_edited_in_the_repository_its_checkout_came_from(editor):
    # A factory the installation hosts has no factory.repository in its config.
    e = editor
    e.definition.repository = lambda: None
    answer = e.fake.__call__

    def factory(method, path, body=None):
        status, data = answer(method, path, body)
        if path == "/integrations/definition":
            data = {**data, "origin": "http://workplace:7301/github/acme/factory.git"}
        return status, data

    e.definition.factory = factory
    assert e.definition.tree()["repository"] == "acme/factory"
    e.definition.save(
        e.admin, _main(e), [Change(path=INSTRUCTIONS, content="After.\n")], None
    )
    assert e.repo.bare.blob("main", INSTRUCTIONS)[1] == b"After.\n"
    e.definition.factory = answer
    e.definition._origin = None
    with pytest.raises(HTTPException) as refused:
        e.definition.tree()
    assert refused.value.status_code == 409


def test_a_skill_is_edited_as_fields_keeping_the_rest_of_its_frontmatter(editor):
    from omniagents_workplace.factory_definition import SkillBody

    e = editor
    path = "agents/foreman/skills/triage/SKILL.md"
    e.w.github.write_files(
        e.repo,
        "admin",
        "main",
        [
            (
                path,
                b"---\nname: triage\ndescription: Old.\nallowed-tools: shell\n---\n\nOld steps.\n",
            )
        ],
        "Skill",
        bypass_protection=True,
    )
    skill = e.definition.skill(path)
    assert (skill["name"], skill["description"], skill["body"]) == (
        "triage",
        "Old.",
        "Old steps.\n",
    )
    e.definition.save_skill(
        e.admin,
        SkillBody(
            base_commit=skill["commit"],
            path=path,
            description="Sort work.",
            body="1. Read it.",
        ),
    )
    text = e.repo.bare.blob("main", path)[1].decode()
    assert (
        text
        == "---\nname: triage\ndescription: Sort work.\nallowed-tools: shell\n---\n\n1. Read it.\n"
    )
    new = "skills/notes/SKILL.md"
    e.definition.save_skill(
        e.admin,
        SkillBody(
            base_commit=_main(e), path=new, description="Take notes.", body="Write."
        ),
    )
    assert e.repo.bare.blob("main", new)[1].startswith(
        b"---\nname: notes\ndescription: Take notes.\n"
    )
    assert e.repo.bare.commit(_main(e)).subject == "Add skill notes"


def test_sharing_a_skill_moves_its_folder_in_one_commit(editor):
    from omniagents_workplace.factory_definition import MoveBody

    e = editor
    e.w.github.write_files(
        e.repo,
        "admin",
        "main",
        [
            (
                "agents/foreman/skills/triage/SKILL.md",
                b"---\nname: triage\ndescription: x\n---\n",
            ),
            ("agents/foreman/skills/triage/references/a.md", b"Ref.\n"),
        ],
        "Skill",
        bypass_protection=True,
    )
    before = _main(e)
    result = e.definition.move_skill(
        e.admin,
        MoveBody(
            base_commit=before,
            path="agents/foreman/skills/triage/SKILL.md",
            to="skills",
            message="Share skill triage with every agent",
        ),
    )
    commit = e.repo.bare.commit(_main(e))
    assert result["published"] is True and commit.parents == [before]
    assert commit.subject == "Share skill triage with every agent"
    assert e.repo.bare.blob("main", "skills/triage/references/a.md")[1] == b"Ref.\n"
    assert e.repo.bare.blob("main", "agents/foreman/skills/triage/SKILL.md") is None
    with pytest.raises(HTTPException) as refused:
        e.definition.move_skill(
            e.admin,
            MoveBody(
                base_commit=_main(e),
                path="skills/triage/SKILL.md",
                to="skills",
                message="x",
            ),
        )
    assert refused.value.status_code == 422


def test_deleting_a_skill_removes_its_folder(editor):
    e = editor
    e.w.github.write_files(
        e.repo,
        "admin",
        "main",
        [
            ("skills/notes/SKILL.md", b"---\nname: notes\ndescription: x\n---\n"),
            ("skills/notes/scripts/run.sh", b"echo\n"),
        ],
        "Skill",
        bypass_protection=True,
    )
    e.definition.delete_skill(e.admin, _main(e), "skills/notes/SKILL.md")
    assert not [f for f in e.repo.bare.files("main") if f.startswith("skills/notes/")]
    assert e.repo.bare.commit(_main(e)).subject == "Delete skill notes"


def test_adding_an_agent_commits_the_files_the_factory_names(editor):
    from omniagents_workplace.factory_definition import AgentBody

    e = editor
    answer = e.fake.__call__
    asked = []

    def factory(method, path, body=None):
        if path == "/integrations/definition/new-agent":
            asked.append(body)
            return 200, {
                "changes": [
                    {
                        "path": "agents/designer/agent.yml",
                        "content": "name: designer\n",
                    },
                    {
                        "path": "agents/designer/instructions.md",
                        "content": "# Designer\n",
                    },
                ]
            }
        return answer(method, path, body)

    e.definition.factory = factory
    result = e.definition.add_agent(
        e.admin,
        AgentBody(key="designer", description="Designs.", template="base", chat=True),
    )
    assert asked == [
        {"key": "designer", "description": "Designs.", "template": "base", "chat": True}
    ]
    assert result["agent"] == "designer" and result["published"] is True
    assert (
        e.repo.bare.blob("main", "agents/designer/agent.yml")[1] == b"name: designer\n"
    )
    assert e.repo.bare.commit(_main(e)).subject == "Add agent Designer"


def test_an_agents_settings_and_deleting_it_are_saved_as_the_factory_says(editor):
    from omniagents_workplace.factory_definition import AgentSettingsBody

    e = editor
    answer = e.fake.__call__
    asked = []

    def factory(method, path, body=None):
        if path == "/integrations/definition/agents/designer":
            return 200, {"key": "designer", "model": None}
        if path == "/integrations/definition/agents/designer/changes":
            asked.append(body)
            if body.get("delete"):
                return 200, {
                    "changes": [{"path": "agents/designer/agent.yml", "content": None}]
                }
            return 200, {
                "changes": [
                    {
                        "path": "agents/designer/agent.yml",
                        "content": "name: designer\nmodel: x\n",
                    }
                ]
            }
        return answer(method, path, body)

    e.definition.factory = factory
    assert e.definition.agent_settings("designer")["key"] == "designer"
    settings = AgentSettingsBody(model="x").model_dump(exclude_unset=True)
    saved = e.definition.change_agent(
        e.admin, "designer", {"settings": settings}, "Change Designer's settings"
    )
    assert asked[0] == {"settings": {"model": "x"}} and saved["published"] is True
    assert (
        e.repo.bare.blob("main", "agents/designer/agent.yml")[1]
        == b"name: designer\nmodel: x\n"
    )
    assert e.repo.bare.commit(_main(e)).subject == "Change Designer's settings"
    e.definition.change_agent(
        e.admin, "designer", {"delete": True}, "Delete agent Designer"
    )
    assert e.repo.bare.blob("main", "agents/designer/agent.yml") is None


def test_an_automation_is_made_changed_and_deleted_as_fields(editor):
    from omniagents_workplace.factory_definition import AutomationBody

    e = editor
    body = dict(
        description="reviews the day",
        agent="improver",
        triggers=[
            {
                "provider": "schedule",
                "schedule": {"name": "nightly", "cron": "0 2 * * *"},
            }
        ],
        instruction="Review the day's runs.",
    )
    e.definition.save_automation(
        e.admin, "nightly", AutomationBody(base_commit=_main(e), **body)
    )
    text = e.repo.bare.blob("main", "automations/nightly/automation.md")[1].decode()
    assert text.startswith(
        "---\ndescription: reviews the day\nagent: improver\ntriggers:\n"
    )
    assert text.endswith("---\n\nReview the day's runs.\n")
    assert e.repo.bare.commit(_main(e)).subject == "Add automation nightly"
    found = e.definition.automation("nightly")
    assert (
        found["fields"]["agent"] == "improver"
        and found["fields"]["instruction"] == "Review the day's runs."
    )
    # Settings the form doesn't show survive a change.
    e.w.github.write_files(
        e.repo,
        "admin",
        "main",
        [
            (
                "automations/nightly/automation.md",
                text.replace("triggers:", "self_improvement: true\ntriggers:").encode(),
            )
        ],
        "Flag",
        bypass_protection=True,
    )
    e.definition.save_automation(
        e.admin,
        "nightly",
        AutomationBody(
            base_commit=_main(e), **{**body, "agent": None, "conversation": "new"}
        ),
    )
    meta = e.repo.bare.blob("main", "automations/nightly/automation.md")[1].decode()
    assert (
        "self_improvement: true" in meta
        and "agent:" not in meta
        and "conversation: new" in meta
    )
    e.w.github.write_files(
        e.repo,
        "admin",
        "main",
        [
            (
                "automations/templated/automation.md",
                b"---\n{% if x %}triggers: []{% endif %}\n---\nGo.\n",
            )
        ],
        "Templated",
        bypass_protection=True,
    )
    assert e.definition.automation("templated")["fields"] is None
    e.definition.delete_automation(e.admin, "nightly")
    assert not [
        f for f in e.repo.bare.files("main") if f.startswith("automations/nightly/")
    ]
