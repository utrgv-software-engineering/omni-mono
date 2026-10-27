from __future__ import annotations

from pathlib import Path
import sys

MODULE_PREFIXES = ("guardrails", "server_functions")

import pytest

from omniagents.core.project.runtime import (
    build_spec_from_project,
    load_project,
    resolve_agent,
)


def _clear_namespaces():
    for name in list(sys.modules):
        for prefix in MODULE_PREFIXES:
            if name == prefix or name.startswith(f"{prefix}."):
                sys.modules.pop(name, None)
                break


@pytest.fixture(autouse=True)
def module_isolation():
    snapshot = {
        name: sys.modules[name]
        for name in list(sys.modules)
        for prefix in MODULE_PREFIXES
        if name == prefix or name.startswith(f"{prefix}.")
    }
    _clear_namespaces()
    try:
        yield
    finally:
        _clear_namespaces()
        sys.modules.update(snapshot)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_runtime_fixture(tmp_path: Path) -> Path:
    _clear_namespaces()
    root = tmp_path / "demo"
    root.mkdir()
    _write(
        root / "project.yml",
        """product:
  name: demo-project
paths:
  agents: agents
  tools: tools
  guardrails: guardrails
  server_functions: server_functions
  evaluations: evaluations
tracing:
  workflow_name: project-run
settings:
  - key: sample
    label: Sample
theme_color: "#abcdef"
layout: minimal
agents:
  entrypoint: alpha
  include:
    - alpha
    - beta*
  exclude:
    - beta_skip
""",
    )
    _write(
        root / "agents" / "alpha" / "agent.yml",
        """name: alpha
label: Alpha Agent
model: gpt-4o-mini
instructions_file: instructions.md
tools:
  - greet_tool
input_guardrails:
  - input_policy
output_guardrails:
  - output_policy
""",
    )
    _write(root / "agents" / "alpha" / "instructions.md", "Alpha instructions")
    _write(
        root / "agents" / "beta" / "agent.yml",
        """name: beta
label: Beta Agent
model: gpt-4.1-mini
instructions: Beta inline
tools:
  - greet_tool
""",
    )
    _write(
        root / "agents" / "beta_skip" / "agent.yml",
        """name: beta_skip
label: Skip Agent
model: gpt-4o
instructions: Skip inline
""",
    )
    _write(root / "tools" / "__init__.py", "")
    _write(
        root / "tools" / "greet.py",
        """from omniagents.core.tools.discovery import function_tool


@function_tool
def greet_tool(name: str = "world"):
    return f"hi {name}"
""",
    )
    _write(root / "guardrails" / "__init__.py", "")
    _write(
        root / "guardrails" / "policies.py",
        """from agents.guardrail import InputGuardrail, OutputGuardrail


def _allow(value):
    return True


def _mirror(value):
    return value


input_policy = InputGuardrail(_allow, name="input_policy")
output_policy = OutputGuardrail(_mirror, name="output_policy")
""",
    )
    _write(root / "server_functions" / "__init__.py", "")
    _write(
        root / "server_functions" / "extras.py",
        """from omniagents.core.server_functions.discovery import server_function
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope


@server_function(
    resource_scope=ResourceScope.AGENT,
    execution_requirements=ExecutionRequirement.NONE,
)
async def ping() -> dict:
    return {"pong": True}
""",
    )
    _write(root / "evaluations" / "README.md", "usage")
    return root


def test_load_project_discovers_resources(tmp_path: Path):
    root = _build_runtime_fixture(tmp_path)
    context = load_project(str(root / "project.yml"))
    assert context.root == root
    assert context.entrypoint == "alpha"
    assert set(context.agents_map.keys()) == {"alpha", "beta"}
    tools_dir = context.paths["tools"][0]
    guardrails_dir = context.paths["guardrails"][0]
    server_functions_dir = context.paths["server_functions"][0]
    assert tools_dir == (root / "tools").resolve()
    assert guardrails_dir == (root / "guardrails").resolve()
    assert server_functions_dir == (root / "server_functions").resolve()
    assert "greet_tool" in context.tool_registry
    assert "input_policy" in context.guardrails_registry["input"]
    assert "output_policy" in context.guardrails_registry["output"]
    assert "ping" in context.server_functions_registry


def test_build_spec_from_project_applies_defaults(tmp_path: Path):
    root = _build_runtime_fixture(tmp_path)
    context = load_project(str(root))
    spec = build_spec_from_project(context, "beta")
    assert spec.project_slug == "demo_project"
    assert spec.agent_slug == "beta"
    assert spec.model_name == "gpt-4.1-mini"
    assert spec.tracing_config.get("workflow_name") == "project-run"
    assert spec.get_agent_instructions() == "Beta inline"


def test_resolve_agent_enforces_valid_keys(tmp_path: Path):
    root = _build_runtime_fixture(tmp_path)
    context = load_project(str(root))
    key, agent_path = resolve_agent(context, "alpha")
    assert key == "alpha"
    assert agent_path == (root / "agents" / "alpha" / "agent.yml").resolve()
    with pytest.raises(FileNotFoundError):
        resolve_agent(context, "unknown")


def test_build_spec_from_project_attaches_voice_spec(tmp_path: Path):
    root = tmp_path / "voice"
    root.mkdir()
    _write(
        root / "project.yml",
        """paths:
  agents: agents
agents:
  entrypoint: primary
  voice_entrypoint: voice_helper
""",
    )
    _write(
        root / "agents" / "primary" / "agent.yml",
        """name: primary
model: gpt-4o
instructions: main
""",
    )
    _write(
        root / "agents" / "voice_helper" / "agent.yml",
        """name: voice_helper
model: gpt-realtime
instructions: voice
realtime_mode: true
""",
    )

    context = load_project(str(root))
    spec = build_spec_from_project(context)

    assert spec.voice_spec is not None
    assert spec.voice_spec.agent_slug == "voice_helper"
    assert spec.voice_spec.model_name == "gpt-realtime"


def test_load_project_rejects_name_mismatch(tmp_path: Path):
    root = tmp_path / "invalid"
    root.mkdir()
    _write(
        root / "project.yml",
        """paths:
  agents: agents
""",
    )
    _write(
        root / "agents" / "bad" / "agent.yml",
        """name: BAD
model: gpt-4o
instructions: nope
""",
    )
    with pytest.raises(ValueError):
        load_project(str(root))


# ---------------------------------------------------------------------------
# One-name conventions: the product identity IS the project identity
# ---------------------------------------------------------------------------


def _write_agent(root: Path, key: str, model: str = "gpt-4o") -> None:
    _write(
        root / "agents" / key / "agent.yml",
        f"""name: {key}
model: {model}
instructions: hello
""",
    )


def test_single_agent_auto_entrypoint(tmp_path: Path):
    """A single-agent project needs no declaration: its sole agent is the
    unambiguous entrypoint."""
    root = tmp_path / "acme"
    root.mkdir()
    _write(root / "project.yml", "product:\n  name: acme-agent\n")
    _write_agent(root, "solo")
    context = load_project(str(root))
    assert context.entrypoint == "solo"


def test_multi_agent_requires_declared_entrypoint(tmp_path: Path):
    """No entrypoint-by-name convention: an agent named after the product's
    prog/slug is NOT implicitly the entrypoint. With several agents and no
    declaration, the entrypoint is unresolved and resolving it fails with
    an error that names the `agents.entrypoint` key."""
    root = tmp_path / "acme"
    root.mkdir()
    _write(
        root / "project.yml",
        """product:
  name: acme-agent
  prog: acme
""",
    )
    _write_agent(root, "acme")
    _write_agent(root, "acme_agent")
    _write_agent(root, "helper")
    context = load_project(str(root))
    assert context.entrypoint is None
    with pytest.raises(FileNotFoundError, match=r"agents:\s*\n\s*entrypoint:"):
        build_spec_from_project(context)


def test_declared_entrypoint_always_wins(tmp_path: Path):
    root = tmp_path / "acme"
    root.mkdir()
    _write(
        root / "project.yml",
        """product:
  name: acme-agent
  prog: acme
agents:
  entrypoint: helper
""",
    )
    _write_agent(root, "acme")
    _write_agent(root, "helper")
    context = load_project(str(root))
    assert context.entrypoint == "helper"


def test_voice_agent_name_is_not_a_voice_entrypoint_convention(tmp_path: Path):
    """An agent literally named `voice_agent` gets no special treatment:
    without a declared `agents.voice_entrypoint` there is no voice spec."""
    root = tmp_path / "acme"
    root.mkdir()
    _write(
        root / "project.yml",
        "product:\n  name: acme\nagents:\n  entrypoint: acme\n",
    )
    _write_agent(root, "acme")
    _write_agent(root, "voice_agent", model="gpt-realtime")
    context = load_project(str(root))
    assert context.entrypoint == "acme"
    spec = build_spec_from_project(context)
    assert spec.voice_spec is None


def test_declared_voice_entrypoint_builds_voice_spec(tmp_path: Path):
    root = tmp_path / "acme"
    root.mkdir()
    _write(
        root / "project.yml",
        """product:
  name: acme
agents:
  entrypoint: acme
  voice_entrypoint: talker
""",
    )
    _write_agent(root, "acme")
    _write_agent(root, "talker", model="gpt-realtime")
    context = load_project(str(root))
    assert context.voice_entrypoint == "talker"
    spec = build_spec_from_project(context)
    assert spec.voice_spec is not None
    assert spec.voice_spec.agent_slug == "talker"


def test_tracing_defaults_derive_from_product_slug(tmp_path: Path):
    root = tmp_path / "acme"
    root.mkdir()
    _write(root / "project.yml", "product:\n  name: acme-agent\n")
    _write_agent(root, "acme_agent")
    context = load_project(str(root))
    assert context.tracing_defaults == {
        "provider": "none",
        "project": "acme_agent",
        "name": "acme_agent",
    }


def test_tracing_declared_keys_merge_over_derived_defaults(tmp_path: Path):
    root = tmp_path / "acme"
    root.mkdir()
    _write(
        root / "project.yml",
        """product:
  name: acme-agent
tracing:
  project: custom_traces
  settings:
    resume: allow
""",
    )
    _write_agent(root, "acme_agent")
    context = load_project(str(root))
    assert context.tracing_defaults["project"] == "custom_traces"
    assert context.tracing_defaults["name"] == "acme_agent"
    assert context.tracing_defaults["provider"] == "none"
    assert context.tracing_defaults["settings"] == {"resume": "allow"}


def test_project_identity_derives_from_directory_without_product(tmp_path: Path):
    root = tmp_path / "dir_named_project"
    root.mkdir()
    _write(root / "project.yml", "paths:\n  agents: agents\n")
    _write_agent(root, "solo")
    context = load_project(str(root))
    assert context.product.slug == "dir_named_project"
    spec = build_spec_from_project(context)
    assert spec.project_slug == "dir_named_project"


def test_legacy_top_level_name_is_inert(tmp_path: Path):
    """Top-level `name:` was removed as a concept — identity never reads it."""
    root = tmp_path / "actual_dir"
    root.mkdir()
    _write(
        root / "project.yml",
        """name: legacy_name
paths:
  agents: agents
""",
    )
    _write_agent(root, "solo")
    context = load_project(str(root))
    assert context.product.slug == "actual_dir"
    assert context.tracing_defaults["project"] == "actual_dir"
    spec = build_spec_from_project(context)
    assert spec.project_slug == "actual_dir"
