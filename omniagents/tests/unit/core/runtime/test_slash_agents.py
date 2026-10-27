"""Declarative slash → project-agent bindings, registered by AgentService as
server functions with a slash declaration and run through server_call."""

from dataclasses import replace

import pytest

from omniagents.core.product import (
    product_info_from_mapping,
    reset_product_info,
    set_product_info,
)
from omniagents.core.runtime import ephemeral, slash_agents

from ..agents.test_environment_run_retirement import make_service

MANIFEST = """\
name: acme
slash_commands:
  init:
    agent: init_agent
    prompt: begin
    max_turns: 1000
    normalize_model: false
    description: Create or update AGENTS.md
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


def test_bindings_become_slash_commands_in_the_catalog(fake_product, monkeypatch):
    service = make_service(monkeypatch)
    catalog = {row["name"]: row for row in service.list_slash_commands()}
    assert catalog["init"]["description"] == "Create or update AGENTS.md"
    assert catalog["init"]["function"] == "init"
    assert catalog["init"]["args"] == {"kind": "none"}
    assert catalog["init"]["during_run"] is False
    assert "bare" in catalog
    # The catalog is the typeable subset, never the whole RPC surface.
    assert "session.ensure" not in catalog
    assert "slash.list" not in catalog
    assert "init" in service._server_functions


@pytest.mark.asyncio
async def test_binding_runs_the_agent_through_server_call(
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
    service = make_service(monkeypatch)
    session = service._session_manager.get_or_create("slash-init")

    result = await service.server_call("init", {}, session.id)

    assert captured["agent_key"] == "init_agent"
    assert captured["prompt"] == "begin"
    assert captured["max_turns"] == 1000
    assert captured["workspace_root"] == str(workspace)
    assert result["message"] == (
        "Created AGENTS.md\n\nAGENTS.md:\n\n```markdown\n# Guide\n\n```"
    )


@pytest.mark.asyncio
async def test_run_failure_returns_declared_failure_message(
    fake_product, workspace, monkeypatch
):
    async def fake_run(agent_key, prompt, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(ephemeral, "run_project_agent_once", fake_run)
    service = make_service(monkeypatch)
    session = service._session_manager.get_or_create("slash-fail")
    assert (await service.server_call("init", {}, session.id))["message"] == (
        "Failed to update AGENTS.md (boom)"
    )
    assert (await service.server_call("bare", {}, session.id))["message"] == (
        "Failed to run /bare. (boom)"
    )


@pytest.mark.asyncio
async def test_slash_list_is_a_server_function(fake_product, monkeypatch):
    service = make_service(monkeypatch)
    session = service._session_manager.get_or_create("slash-list")
    result = await service.server_call("slash.list", {}, session.id)
    names = [row["name"] for row in result["commands"]]
    assert "init" in names
    orders = [row["order"] for row in result["commands"]]
    assert orders == sorted(orders)
