"""The factory's definition as people edit it: what is editable, checking edits, advancing."""

import subprocess
import textwrap
from pathlib import Path

import pytest

from omniagents.integrations.definition import (
    DefinitionError,
    advance,
    check_changes,
    definition_tree,
    live_commit,
    origin,
    read_each_run,
    skill_source,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip())


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def factory(tmp_path):
    """A factory checkout (``live``) of a repository (``origin``), at its first commit."""
    source = tmp_path / "source"
    _write(
        source / "project.yml",
        """
        agents: {entrypoint: foreman}
        paths:
          agents: [agents, base/agents]
    """,
    )
    _write(
        source / "base/agent.yml", "name: base\ninstructions_file: instructions.md\n"
    )
    _write(source / "base/instructions.md", "Base.\n\n{{ role }}\n")
    _write(
        source / "agents/foreman/agent.yml",
        "name: foreman\ndescription: Runs the line.\nextends: base\ninstructions_file: instructions.md\n",
    )
    _write(source / "agents/foreman/instructions.md", "Foreman.\n")
    _write(
        source / "agents/foreman/skills/triage/SKILL.md",
        "---\nname: triage\ndescription: Sort incoming work.\n---\n\nSteps.\n",
    )
    _write(
        source / "skills/shared-notes/SKILL.md",
        "---\nname: shared-notes\ndescription: Keep notes.\n---\n\nSteps.\n",
    )
    _write(
        source / "automations/nightly/automation.md",
        """
        ---
        description: reviews the day
        triggers:
          - provider: schedule
            schedule: {name: nightly, cron: "0 2 * * *"}
        ---

        Review the day.
    """,
    )
    _git(source, "init", "-q", "-b", "main")
    _git(source, "add", "-A")
    _git(source, "commit", "-qm", "Start")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(source), str(origin))
    live = tmp_path / "live"
    _git(tmp_path, "clone", "-q", str(origin), str(live))
    return {"source": source, "origin": origin, "live": live}


def _commit_in_source(factory, path: str, text: str) -> str:
    _write(factory["source"] / path, text)
    _git(factory["source"], "add", "-A")
    _git(factory["source"], "commit", "-qm", f"Change {path}")
    _git(factory["source"], "push", "-q", str(factory["origin"]), "main")
    return _git(factory["source"], "rev-parse", "HEAD")


def test_what_is_read_each_run():
    assert read_each_run("agents/foreman/instructions.md")
    assert read_each_run("base/fragments/task.md")
    assert read_each_run("agents/foreman/skills/triage/scripts/run.py")
    assert read_each_run("automations/nightly/automation.md")
    assert not read_each_run("agents/foreman/agent.yml")
    assert not read_each_run("project.yml")
    assert not read_each_run("uv.lock")
    assert not read_each_run("evaluations/judges/quality/instructions.md")


def test_tree_lists_agents_bases_skills_and_automations(factory):
    tree = definition_tree(factory["live"])
    (foreman,) = tree["agents"]
    assert foreman["key"] == "foreman"
    assert foreman["description"] == "Runs the line."
    assert foreman["instructions"] == "agents/foreman/instructions.md"
    assert foreman["skills"] == [
        {
            "name": "triage",
            "path": "agents/foreman/skills/triage/SKILL.md",
            "description": "Sort incoming work.",
        }
    ]
    assert foreman["chat"] is False and foreman["extends_chain"] == ["base"]
    assert [b["key"] for b in tree["bases"]] == ["base"]
    assert tree["bases"][0]["instructions"] == "base/instructions.md"
    assert tree["bases"][0]["agents"] == ["foreman"]
    assert tree["skills"] == [
        {
            "folder": "skills",
            "skills": [
                {
                    "name": "shared-notes",
                    "path": "skills/shared-notes/SKILL.md",
                    "description": "Keep notes.",
                }
            ],
        }
    ]
    assert tree["automations"] == [
        {"name": "nightly", "path": "automations/nightly/automation.md"}
    ]


def test_check_accepts_good_edits_and_new_files(factory):
    assert (
        check_changes(
            factory["live"],
            [
                {"path": "agents/foreman/instructions.md", "content": "New {{ role }}"},
                {
                    "path": "agents/foreman/skills/notes/SKILL.md",
                    "content": "---\nname: notes\ndescription: Take notes.\n---\n\nSteps.\n",
                },
                {
                    "path": "automations/weekly/automation.md",
                    "content": "---\ntriggers: []\n---\n\nDo it.\n",
                },
                {"path": "skills/shared-notes/SKILL.md", "content": None},
            ],
        )
        == {}
    )


def test_check_says_what_is_wrong(factory):
    problems = check_changes(
        factory["live"],
        [
            {"path": "agents/foreman/agent.yml", "content": "model: x\n"},
            {"path": "agents/foreman/instructions.md", "content": "{% if %}"},
            {"path": "base/instructions.md", "content": None},
            {
                "path": "agents/foreman/skills/notes/SKILL.md",
                "content": "---\nname: other\ndescription: x\n---\n",
            },
            {"path": "automations/weekly/automation.md", "content": "no frontmatter"},
            {"path": "../outside.md", "content": "x"},
        ],
    )
    assert "Only agents' instructions" in problems["agents/foreman/agent.yml"][0]
    assert any("template" in p for p in problems["agents/foreman/instructions.md"])
    assert "emptied, not deleted" in problems["base/instructions.md"][0]
    assert (
        "must match skill name" in problems["agents/foreman/skills/notes/SKILL.md"][0]
    )
    assert "frontmatter" in problems["automations/weekly/automation.md"][0]
    assert str(factory["live"]) not in problems["automations/weekly/automation.md"][0]
    assert problems["../outside.md"]


def test_advance_moves_to_an_edit(factory):
    live = factory["live"]
    commit = _commit_in_source(factory, "agents/foreman/instructions.md", "Edited.\n")
    assert advance(live, commit) == commit
    assert live_commit(live) == commit
    assert (live / "agents/foreman/instructions.md").read_text() == "Edited.\n"
    assert advance(live, commit) == commit  # again: nothing to do


def test_advance_refuses_what_needs_a_release(factory):
    live = factory["live"]
    before = live_commit(live)
    commit = _commit_in_source(
        factory, "agents/foreman/agent.yml", "name: foreman\nmodel: x\n"
    )
    with pytest.raises(DefinitionError) as refused:
        advance(live, commit)
    assert refused.value.status == 422
    assert refused.value.paths == ["agents/foreman/agent.yml"]
    assert live_commit(live) == before


def test_advance_refuses_uncommitted_changes(factory):
    live = factory["live"]
    commit = _commit_in_source(factory, "agents/foreman/instructions.md", "Edited.\n")
    (live / "base/instructions.md").write_text("Changed on the server.\n")
    with pytest.raises(DefinitionError) as refused:
        advance(live, commit)
    assert refused.value.status == 409
    assert refused.value.paths == ["base/instructions.md"]


def test_advance_refuses_a_commit_that_does_not_follow(factory):
    live = factory["live"]
    _write(
        live / "automations/nightly/automation.md", "---\ntriggers: []\n---\n\nLocal.\n"
    )
    _git(live, "commit", "-qam", "Local")
    commit = _commit_in_source(factory, "agents/foreman/instructions.md", "Edited.\n")
    with pytest.raises(DefinitionError) as refused:
        advance(live, commit)
    assert refused.value.status == 409
    assert "does not follow" in str(refused.value)


def test_advance_needs_a_checkout(tmp_path):
    with pytest.raises(DefinitionError) as refused:
        advance(tmp_path, "a" * 40)
    assert refused.value.status == 409


def test_origin_names_the_repository_without_credentials(factory):
    live = factory["live"]
    assert origin(live) == str(factory["origin"])
    _git(
        live,
        "remote",
        "set-url",
        "origin",
        "http://x:secret@workplace:7301/github/acme/factory.git",
    )
    assert origin(live) == "http://workplace:7301/github/acme/factory.git"


def test_an_agents_skills_say_who_has_them_and_what_they_replace(factory):
    live = factory["live"]
    _write(
        live / "skills/triage/SKILL.md",
        "---\nname: triage\ndescription: Everyone's triage.\n---\n",
    )
    _write(
        live / "base/skills/house-style/SKILL.md",
        "---\nname: house-style\ndescription: Write like us.\n---\n",
    )
    (foreman,) = definition_tree(live)["agents"]
    by_name = {s["name"]: s for s in foreman["effective_skills"]}
    assert [s["name"] for s in foreman["effective_skills"]] == [
        "triage",
        "house-style",
        "shared-notes",
    ]
    assert by_name["triage"]["scope"] == {"kind": "agent", "agent": "foreman"}
    assert by_name["triage"]["replaces"] == [
        {"path": "skills/triage/SKILL.md", "scope": {"kind": "factory"}}
    ]
    assert by_name["house-style"]["scope"] == {
        "kind": "base",
        "base": "base",
        "name": "Base",
    }
    assert by_name["shared-notes"]["scope"] == {"kind": "factory"}


def test_a_skills_other_files_can_change_with_it(factory):
    assert (
        check_changes(
            factory["live"],
            [
                {"path": "skills/shared-notes/SKILL.md", "content": None},
                {"path": "skills/shared-notes/references/a.md", "content": None},
                {
                    "path": "agents/foreman/skills/shared-notes/SKILL.md",
                    "content": "---\nname: shared-notes\ndescription: Keep notes.\n---\n",
                },
                {
                    "path": "agents/foreman/skills/shared-notes/references/a.md",
                    "content": "x",
                },
            ],
        )
        == {}
    )


def test_where_a_runs_skill_came_from(factory):
    live = factory["live"]
    assert skill_source(live, str(live / "agents/foreman/skills/triage")) == {
        "kind": "agent",
        "agent": "foreman",
    }
    assert skill_source(live, str(live / "skills/shared-notes")) == {"kind": "factory"}
    assert skill_source(live, str(live / "base/skills/house-style")) == {
        "kind": "base",
        "base": "base",
    }
    assert skill_source(live, "/workspace/webapp/.omniagents/skills/lint") == {
        "kind": "repository",
        "repository": "webapp",
    }
    assert skill_source(live, "/workspace/webapp/skills/lint") == {
        "kind": "repository",
        "repository": "webapp",
    }
