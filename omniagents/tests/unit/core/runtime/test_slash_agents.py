"""Declarative slash → project-agent bindings, end-to-end through the
default slash registry with a faked agent run."""

from dataclasses import replace

import pytest

from omniagents.core.product import (
    product_info_from_mapping,
    reset_product_info,
    set_product_info,
)
from omniagents.core.runtime import ephemeral, slash_agents
from omniagents.core.tools import default_slash_registry, register_slash_command

MANIFEST = """\
name: acme
slash_commands:
  init:
    agent: init_agent
    prompt: begin
    max_turns: 1000
    normalize_model: false
    failure_message: Failed to update AGENTS.md
    attach_file:
      path: AGENTS.md
      when_status:
        - Created AGENTS.md
        - Updated AGENTS.md
      language: markdown
  bare:
    agent: bare_agent
"""


@pytest.fixture
def fake_product(tmp_path):
    manifest = tmp_path / "project.yml"
    manifest.write_text(MANIFEST, encoding="utf-8")
    info = replace(product_info_from_mapping({"name": "acme"}), manifest_path=manifest)
    set_product_info(info)
    yield tmp_path
    reset_product_info()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("PWD", str(ws))
    return ws


def test_manifest_slash_bindings_reads_declaration(fake_product):
    bindings = slash_agents.manifest_slash_bindings()
    assert set(bindings) == {"init", "bare"}
    assert bindings["init"]["agent"] == "init_agent"
    assert bindings["init"]["max_turns"] == 1000
    assert bindings["init"]["normalize_model"] is False


def test_manifest_slash_bindings_empty_without_product():
    reset_product_info()
    assert slash_agents.manifest_slash_bindings() == {}


@pytest.mark.asyncio
async def test_registry_falls_back_to_manifest_binding(
    fake_product, workspace, monkeypatch
):
    captured = {}

    async def fake_run(agent_key, prompt, **kwargs):
        captured["agent_key"] = agent_key
        captured["prompt"] = prompt
        captured.update(kwargs)
        return "Created AGENTS.md"

    monkeypatch.setattr(ephemeral, "run_project_agent_once", fake_run)
    (workspace / "AGENTS.md").write_text("# Guide\n", encoding="utf-8")

    registry = default_slash_registry()
    assert registry.has("init")
    output = await registry.execute("init")

    assert captured["agent_key"] == "init_agent"
    assert captured["prompt"] == "begin"
    assert captured["max_turns"] == 1000
    assert captured["workspace_root"] == str(workspace)
    # Success-status + existing file => attach_file protocol formatting
    # (byte-identical to the handler this replaces: the body keeps its
    # own trailing newline ahead of the closing fence's).
    assert output == "Created AGENTS.md\n\nAGENTS.md:\n\n```markdown\n# Guide\n\n```"


@pytest.mark.asyncio
async def test_status_outside_when_status_returned_verbatim(
    fake_product, workspace, monkeypatch
):
    async def fake_run(agent_key, prompt, **kwargs):
        return "Failed to create AGENTS.md"

    monkeypatch.setattr(ephemeral, "run_project_agent_once", fake_run)
    (workspace / "AGENTS.md").write_text("# Guide\n", encoding="utf-8")
    output = await default_slash_registry().execute("init")
    assert output == "Failed to create AGENTS.md"


@pytest.mark.asyncio
async def test_run_failure_returns_declared_failure_message(
    fake_product, workspace, monkeypatch
):
    async def fake_run(agent_key, prompt, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(ephemeral, "run_project_agent_once", fake_run)
    assert await default_slash_registry().execute("init") == (
        "Failed to update AGENTS.md"
    )
    # Binding without failure_message gets the generic text.
    assert await default_slash_registry().execute("bare") == "Failed to run /bare."


@pytest.mark.asyncio
async def test_python_registered_handler_wins_over_binding(fake_product, workspace):
    registry = default_slash_registry()
    register_slash_command("init", lambda _arg=None: "python handler")
    try:
        assert await registry.execute("init") == "python handler"
    finally:
        registry._commands.pop("init", None)


@pytest.mark.asyncio
async def test_unknown_command_still_unknown(fake_product):
    assert await default_slash_registry().execute("nope") == "Unknown command: nope"
    assert not default_slash_registry().has("nope")
