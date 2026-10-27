"""A project's own values in its text: ``{{ project.* }}`` in instructions and automations."""

import asyncio

import pytest

from omniagents.core.automations import AutomationError, load_automation
from omniagents.core.project.values import project_values_at
from omniagents.core.utils.jinja_instructions import process_instructions

PROJECT = """\
product:
  name: initech-factory
  label: Initech Factory
integrations:
  - type: github
    repositories: [initech/tps]
    routing_label: "factory:Initech"
    account: initech-factory
    git_url: ${GIT_URL:-http://git.test}
    token_env: GITHUB_TOKEN
    webhook_secret: ${WEBHOOK_SECRET}
  - type: slack
    app_token: ${SLACK_APP_TOKEN}
    api_url: https://slack.test/api
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-1")
    (tmp_path / "project.yml").write_text(PROJECT)
    return tmp_path


def test_the_values_name_the_project_and_carry_no_credentials(project):
    values = project_values_at(project)
    assert values["name"] == "initech-factory" and values["label"] == "Initech Factory"
    assert values["github"] == {
        "repositories": ["initech/tps"],
        "routing_label": "factory:Initech",
        "account": "initech-factory",
        "git_url": "http://git.test",
    }
    assert values["slack"] == {"api_url": "https://slack.test/api"}
    assert "s3cret" not in str(values) and "xapp-1" not in str(values)


def test_an_automation_is_rendered_with_them(project):
    folder = project / "automations" / "mentions"
    folder.mkdir(parents=True)
    (folder / "automation.md").write_text(
        "---\ntriggers:\n  - provider: github\n    event: issue_mentioned\n    filter:\n"
        "      repos: {{ project.github.repositories | tojson }}\n"
        '      mentioned: ["{{ project.github.account }}"]\n'
        '      labels: ["{{ project.github.routing_label }}"]\n---\n'
        "You work for {{ project.label }}.\n"
    )
    automation = load_automation(project, "mentions")
    assert automation.triggers[0]["filter"] == {
        "repos": ["initech/tps"],
        "mentioned": ["initech-factory"],
        "labels": ["factory:Initech"],
    }
    assert automation.instruction == "You work for Initech Factory."

    (folder / "automation.md").write_text(
        "---\ntriggers:\n  - provider: github\n    event: issue_mentioned\n    filter:\n"
        '      mentioned: ["{{ project.github.acount }}"]\n---\nGo.\n'
    )
    with pytest.raises(
        AutomationError, match="acount"
    ):  # a typo is an error, not a silent ""
        load_automation(project, "mentions")


def test_instructions_have_them_in_scope_under_the_runs_context(project):
    render = process_instructions(
        "{{ project.label }} on {{ project.github.repositories | join(', ') }}; {{ who }}",
        globals={"project": project_values_at(project), "who": "global"},
    )
    context = type("Wrapper", (), {"context": {"who": "the run"}})()
    assert (
        asyncio.run(render(context, None)) == "Initech Factory on initech/tps; the run"
    )
