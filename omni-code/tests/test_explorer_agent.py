"""Product-owned explorer agent + exploration capability pack.

The framework ships capability packs, tools, and prompt seams — but no
agents. omni-code owns the read-only `explorer` sub-agent
(``omni_code/agents/explorer/``) and the ``exploration`` capability pack
(``omni_code/capabilities/exploration.yml``) that exposes it as the
`explore` agent tool. These tests pin the pack wiring and the LLM-facing
contract (tool name, max_turns, no approval, read-only surface) that the
behavior evals were tuned against.
"""

from pathlib import Path

import pytest
import yaml
from omniagents.core.project.runtime import (
    build_spec_from_project,
    load_project,
    resolve_agent,
)
from omniagents.core.utils.jinja_instructions import (
    instruction_search_paths,
    render_template,
)

ROOT = Path(__file__).resolve().parents[1]
EXPLORER_YML = (ROOT / "omni_code" / "agents" / "explorer" / "agent.yml").resolve()


@pytest.fixture(scope="module")
def project():
    return load_project(str(ROOT / "omni_code" / "project.yml"))


def test_explorer_resolves_to_product_definition(project):
    """`explorer` is a product agent: it must resolve to the spec inside
    the omni_code package, never to anything shipped by the framework."""
    key, yaml_path = resolve_agent(project, "explorer")
    assert key == "explorer"
    assert yaml_path == EXPLORER_YML
    assert (yaml_path.parent / "instructions.md").is_file()


def test_exploration_pack_wires_explore_tool(project):
    """The project `exploration` capability exposes the product explorer
    as an `explore` agent tool — same contract the builtin pack carried."""
    cap = project.capability_registry["exploration"]
    assert cap.tools() == []
    configs = cap.agent_tool_configs
    assert len(configs) == 1
    cfg = configs[0]
    assert cfg.tool_name == "explore"
    assert Path(cfg.yaml_path).resolve() == EXPLORER_YML
    assert cfg.needs_approval is False
    assert cfg.max_turns == 50
    assert "explore" in (cap.instructions_text or "")


def test_omni_spec_inherits_explore_from_exploration_pack(project):
    """The production omni agent lists the exploration capability and
    gets exactly one `explore` agent tool pointed at the product spec."""
    spec = build_spec_from_project(project, "omni")
    explore = [c for c in spec.agent_tool_configs if c.tool_name == "explore"]
    assert len(explore) == 1
    cfg = explore[0]
    assert Path(cfg.yaml_path).resolve() == EXPLORER_YML
    assert cfg.max_turns == 50


def test_eval_subagent_test_explorer_agent_tool_resolves(project):
    """eval_subagent_test wires `explore` via a registry reference
    (`agent: explorer`); it must resolve to the product spec too."""
    spec = build_spec_from_project(project, "eval_subagent_test")
    explore = [c for c in spec.agent_tool_configs if c.tool_name == "explore"]
    assert len(explore) == 1
    assert Path(explore[0].yaml_path).resolve() == EXPLORER_YML
    assert explore[0].max_turns == 50


def test_explorer_llm_contract_is_unchanged():
    """The spec-level LLM contract the behavior evals were tuned against:
    read-only capability surface, headless, turn budget, and the
    tool_description fallback the `explore` tool presents to the model."""
    data = yaml.safe_load(EXPLORER_YML.read_text(encoding="utf-8"))
    assert data["name"] == "explorer"
    assert data["max_turns"] == 50
    assert data["use_safe_agent"] is False
    # Read-only surface only.
    assert data["capabilities"] == [
        "filesystem_readonly",
        "shell_readonly",
        "notebook_readonly",
    ]
    # No product model resolver: the sub-agent inherits the
    # orchestrator's resolved model.
    assert "model_config_resolver" not in data
    assert data.get("model") in (None, "")
    # The LLM-facing description (agent_tools fallback) keeps its anchor
    # phrases.
    desc = data["tool_description"]
    assert "read-only sub-agent" in desc
    assert "explore the workspace" in desc
    assert "multiple explore calls in parallel" in desc


def test_explorer_instructions_render_non_empty():
    anchor = EXPLORER_YML.parent
    template = (anchor / "instructions.md").read_text(encoding="utf-8")
    rendered = render_template(
        template,
        silent_undefined=True,
        search_paths=instruction_search_paths(anchor),
        current_date="2026-01-01",
    )
    assert rendered.strip()
    assert "You are an explorer — a fast, read-only research agent." in rendered
