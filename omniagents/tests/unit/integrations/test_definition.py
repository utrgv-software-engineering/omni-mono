"""The factory's definition as people edit it: what is editable, checking edits, advancing."""

import subprocess
import textwrap
from pathlib import Path

import pytest
from yaml import safe_load as yaml_load

from omniagents.integrations.definition import (
    DefinitionError,
    advance,
    agent_changes,
    agent_settings,
    check_changes,
    delete_agent_changes,
    definition_tree,
    live_commit,
    new_agent_changes,
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
            {"path": "agents/foreman/tools/run.py", "content": "x = 1\n"},
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
    assert problems["agents/foreman/tools/run.py"] == ["Code changes with a release."]
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
    assert advance(live, commit) == {"commit": commit, "reload": False}
    assert live_commit(live) == commit
    assert (live / "agents/foreman/instructions.md").read_text() == "Edited.\n"
    assert advance(live, commit)["commit"] == commit  # again: nothing to do


def test_advance_refuses_what_needs_a_release(factory):
    live = factory["live"]
    before = live_commit(live)
    commit = _commit_in_source(
        factory, "base/context.py", "def build():\n    return {}\n"
    )
    with pytest.raises(DefinitionError) as refused:
        advance(live, commit)
    assert refused.value.status == 422
    assert refused.value.paths == ["base/context.py"]
    assert live_commit(live) == before


def test_agent_settings_apply_by_reloading(factory):
    live = factory["live"]
    commit = _commit_in_source(
        factory,
        "agents/foreman/agent.yml",
        "name: foreman\ndescription: Runs it.\nextends: base\n"
        "instructions_file: instructions.md\nmodel: other\n",
    )
    assert advance(live, commit) == {"commit": commit, "reload": True}
    problems = check_changes(
        live,
        [{"path": "agents/foreman/agent.yml", "content": "description: no name\n"}],
    )
    assert problems["agents/foreman/agent.yml"]


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


def _commit_changes_in_source(factory, changes, message):
    for change in changes:
        _write(factory["source"] / change["path"], change["content"])
    _git(factory["source"], "add", "-A")
    _git(factory["source"], "commit", "-qm", message)
    _git(factory["source"], "push", "-q", str(factory["origin"]), "main")
    return _git(factory["source"], "rev-parse", "HEAD")


def test_adding_an_agent_in_chat_is_checked_and_reloads_the_factory(factory):
    live = factory["live"]
    (factory["source"] / "project.yml").write_text(
        (factory["source"] / "project.yml")
        .read_text()
        .replace(
            "agents: {entrypoint: foreman}",
            "agents:\n  entrypoint: foreman  # what automations run\n  chat_agents: [foreman]",
        )
    )
    _git(factory["source"], "commit", "-qam", "Chat")
    _git(factory["source"], "push", "-q", str(factory["origin"]), "main")
    _git(live, "pull", "-q")
    changes = new_agent_changes(
        live,
        "designer",
        description="Turns requests into designs.",
        template="base",
        chat=True,
    )
    by_path = {c["path"]: c["content"] for c in changes}
    assert yaml_load(by_path["agents/designer/agent.yml"]) == {
        "name": "designer",
        "description": "Turns requests into designs.",
        "extends": "base",
        "instructions_file": "instructions.md",
    }
    assert "# what automations run" in by_path["project.yml"]  # comments kept
    assert yaml_load(by_path["project.yml"])["agents"]["chat_agents"] == [
        "foreman",
        "designer",
    ]
    assert check_changes(live, changes) == {}
    commit = _commit_changes_in_source(factory, changes, "Add agent Designer")
    assert advance(live, commit) == {"commit": commit, "reload": True}


def test_adding_an_agent_is_refused_when_it_would_not_load(factory):
    live = factory["live"]
    with pytest.raises(DefinitionError) as refused:
        new_agent_changes(live, "Designer", description="x")
    assert refused.value.status == 422
    with pytest.raises(DefinitionError) as refused:
        new_agent_changes(live, "foreman", description="x")
    assert refused.value.status == 409
    broken = [
        {
            "path": "agents/designer/agent.yml",
            "content": "name: designer\nextends: nowhere\n",
        },
    ]
    problems = check_changes(live, broken)
    assert "nowhere" in " ".join(problems["agents/designer/agent.yml"])
    assert str(live) not in " ".join(problems["agents/designer/agent.yml"])


def test_other_project_settings_still_need_a_release(factory):
    live = factory["live"]
    text = (live / "project.yml").read_text() + "tracing: {provider: studio}\n"
    assert check_changes(live, [{"path": "project.yml", "content": text}])[
        "project.yml"
    ]
    commit = _commit_in_source(factory, "project.yml", text)
    with pytest.raises(DefinitionError) as refused:
        advance(live, commit)
    assert refused.value.paths == ["project.yml"]


def _with_designer(factory):
    """foreman and a designer it calls, both in Chat; foreman the default."""
    live = factory["live"]
    _write(
        factory["source"] / "project.yml",
        """
        agents:
          entrypoint: foreman
          chat_entrypoint: foreman
          chat_agents: [foreman, designer]  # who people talk to
        paths:
          agents: [agents, base/agents]
    """,
    )
    _write(
        factory["source"] / "agents/designer/agent.yml",
        "name: designer  # draws\ndescription: Designs.\nextends: base\n"
        "instructions_file: instructions.md\n",
    )
    _write(factory["source"] / "agents/designer/instructions.md", "Design.\n")
    _write(
        factory["source"] / "agents/designer/tools/draw.py", "def draw():\n    pass\n"
    )
    _commit_in_source(
        factory,
        "agents/foreman/agent.yml",
        "name: foreman\ndescription: Runs the line.\nextends: base\n"
        "instructions_file: instructions.md\nagent_tools:\n"
        "  - agent: designer\n    tool_name: design\n",
    )
    _git(live, "pull", "-q")
    return live


def test_an_agents_settings_as_fields(factory):
    live = _with_designer(factory)
    designer = agent_settings(live, "designer")
    assert designer["description"] == "Designs."
    assert designer["chat"] is True and designer["chat_default"] is False
    assert designer["called_by"] == ["foreman"]
    assert agent_settings(live, "foreman")["calls"] == ["designer"]
    assert {"key": "foreman", "name": "Foreman"} in designer["options"]["agents"]


def test_changing_settings_keeps_the_rest_and_checks(factory):
    live = _with_designer(factory)
    changes = agent_changes(
        live,
        "designer",
        {
            "label": "Designer",
            "model": "gpt-x",
            "description": "Draws screens.",
            "chat": False,
        },
    )
    by_path = {c["path"]: c["content"] for c in changes}
    assert "# draws" in by_path["agents/designer/agent.yml"]
    assert yaml_load(by_path["agents/designer/agent.yml"])["model"] == "gpt-x"
    assert "# who people talk to" in by_path["project.yml"]
    assert yaml_load(by_path["project.yml"])["agents"]["chat_agents"] == ["foreman"]
    assert check_changes(live, changes) == {}
    calls = agent_changes(live, "foreman", {"calls": []})
    assert "agent_tools" not in yaml_load(calls[0]["content"])
    with pytest.raises(DefinitionError):
        agent_changes(live, "foreman", {"chat": False})  # Chat's default


def test_deleting_an_agent_removes_its_folder_and_place_in_chat(factory):
    live = _with_designer(factory)
    with pytest.raises(DefinitionError) as refused:
        delete_agent_changes(live, "designer")
    assert "foreman" in str(refused.value)  # foreman calls it
    unhook = agent_changes(live, "foreman", {"calls": []})
    _commit_changes_in_source(factory, unhook, "Unhook")
    _git(live, "pull", "-q")
    changes = delete_agent_changes(live, "designer")
    assert {c["path"] for c in changes} == {
        "agents/designer/agent.yml",
        "agents/designer/instructions.md",
        "agents/designer/tools/draw.py",
        "project.yml",
    }
    assert check_changes(live, changes) == {}
    for change in changes:
        target = factory["source"] / change["path"]
        if change["content"] is None:
            target.unlink()
        else:
            target.write_text(change["content"])
    _git(factory["source"], "add", "-A")
    _git(factory["source"], "commit", "-qm", "Delete agent Designer")
    _git(factory["source"], "push", "-q", str(factory["origin"]), "main")
    commit = _git(factory["source"], "rev-parse", "HEAD")
    assert advance(live, commit) == {"commit": commit, "reload": True}
    with pytest.raises(DefinitionError):
        delete_agent_changes(live, "foreman")  # the defaults
