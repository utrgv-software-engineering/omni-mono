"""Scenarios that start the way production does: `agent:` and `automation:` + `event:`."""

import asyncio
import json
import textwrap
from pathlib import Path

import pytest

from omniagents.core.automations import (
    AutomationError,
    list_automations,
    load_automation,
)
from omniagents.core.eval import targets
from omniagents.core.eval.environment import NoOpEnvironment
from omniagents.core.eval.targets import ProjectAgentTarget, resolve_scenario_start


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip())


@pytest.fixture
def project(tmp_path):
    _write(tmp_path / "project.yml", "agents: {entrypoint: foreman}\n")
    _write(
        tmp_path / "agents/foreman/agent.yml",
        """
        name: foreman
        instructions: "Delegate."
        agent_tools:
          - agent: reviewer
            tool_name: review
    """,
    )
    _write(
        tmp_path / "agents/reviewer/agent.yml",
        "name: reviewer\ninstructions: Review.\n",
    )
    _write(
        tmp_path / "automations/issue-labeled/automation.md",
        """
        ---
        description: An issue was labelled for the factory.
        triggers:
          - provider: github
            event: issue_labeled
        ---
        An issue was labelled. Drive it to a pull request.
    """,
    )
    _write(
        tmp_path / "automations/pr-opened/automation.md",
        """
        ---
        agent: reviewer
        triggers: [{provider: github, event: pull_request_opened}]
        ---
        Review the pull request.
    """,
    )
    return tmp_path


def _target(project):
    return ProjectAgentTarget(
        project / "agents/foreman/agent.yml", project_root=project
    )


def test_automation_files_load(project):
    auto = load_automation(project, "pr-opened")
    assert (auto.agent, auto.instruction, auto.triggers[0]["event"]) == (
        "reviewer",
        "Review the pull request.",
        "pull_request_opened",
    )
    assert [a.name for a in list_automations(project)] == ["issue-labeled", "pr-opened"]


def test_run_input_is_the_instruction_and_the_event(project):
    text = load_automation(project, "issue-labeled").run_input(
        {"issue": {"number": 12}}
    )
    assert text.startswith(
        "An issue was labelled. Drive it to a pull request.\n\n## Event"
    )
    assert json.loads(text.split("```json\n", 1)[1].rsplit("\n```", 1)[0]) == {
        "issue": {"number": 12}
    }


def test_automation_without_agent_starts_the_entrypoint(project):
    target, scenario = resolve_scenario_start(
        _target(project),
        {
            "name": "s",
            "automation": "issue-labeled",
            "event": {"issue": {"number": 12}},
        },
    )
    assert target.agent_yaml == (project / "agents/foreman/agent.yml").resolve()
    assert scenario["agent"] == "foreman" and "## Event" in scenario["prompt"]


def test_automation_with_agent_starts_that_agent(project):
    target, scenario = resolve_scenario_start(
        _target(project),
        {
            "name": "s",
            "automation": "pr-opened",
            "event": {"pull_request": {"number": 33}},
        },
    )
    assert target.agent_yaml == (project / "agents/reviewer/agent.yml").resolve()
    assert scenario["prompt"].startswith("Review the pull request.")


def test_agent_key_runs_that_agent_with_its_prompt(project):
    target, scenario = resolve_scenario_start(
        _target(project), {"name": "s", "agent": "reviewer", "prompt": "Review #33."}
    )
    assert target.agent_yaml == (project / "agents/reviewer/agent.yml").resolve()
    assert scenario["prompt"] == "Review #33."


def test_plain_scenarios_are_unchanged(project):
    target = _target(project)
    scenario = {"name": "s", "prompt": "hi"}
    assert resolve_scenario_start(target, scenario) == (target, scenario)


def test_automation_scenarios_take_no_prompt(project):
    with pytest.raises(ValueError, match="remove 'prompt'"):
        resolve_scenario_start(
            _target(project), {"name": "s", "automation": "pr-opened", "prompt": "x"}
        )


def test_unknown_agent_and_automation_are_clear_errors(project):
    with pytest.raises(ValueError, match="not in the project"):
        resolve_scenario_start(
            _target(project), {"name": "s", "agent": "nobody", "prompt": "x"}
        )
    with pytest.raises(AutomationError, match="Available"):
        resolve_scenario_start(_target(project), {"name": "s", "automation": "nothing"})


def test_eval_target_builds_agents_like_the_project_runtime(project, monkeypatch):
    # agent_tools by name need the project's agent registry; before, an
    # evaluation built the foreman without its stage tools.
    built = {}

    async def fake_create(spec, **kwargs):
        built["spec"] = spec
        built["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(targets, "create_unified_agent", fake_create)
    asyncio.run(
        _target(project).start(
            {"name": "s", "prompt": "x"},
            defaults={},
            environment=NoOpEnvironment(),
            context=None,
        )
    )
    assert [c.tool_name for c in built["spec"].agent_tool_configs] == ["review"]
    assert built["spec"].use_safe_agent is True  # the agent's own setting stands
    # Nobody watches an evaluation run: approvals go to the guardian, and
    # one it passes on to a person is rejected instead of waiting.
    assert built["kwargs"]["approvals_reviewer"] == "auto"
    assert built["kwargs"]["unattended"] is True
