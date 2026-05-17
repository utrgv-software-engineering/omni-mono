"""Phase 0 acceptance tests for capability packs.

Covers:
* Inline `capabilities:` block in project.yml resolves into a capability
  registry that the agent loader can read.
* Per-agent `capabilities:` list pulls cap.tools() into the agent's tool list.
* `paths.capabilities/` directory discovery works.
* Bare-string capability reference is sugar for `{name: ...}`.
* Per-agent override syntax: approvals (replace), instructions (append).
* Tool-name conflict across capabilities → error.
* Capability `approvals:` block sets `tool.needs_approval` on the matching tool.
* Mixed mode: `tools:` list and `capabilities:` list coexist.
* Missing capability_registry → clear warning, agent loads without caps.
* Unknown capability name → clear warning, skipped.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any, Dict

import pytest

from omniagents.core.capabilities import (
    parse_capability_spec,
    discover_capabilities_in_dir,
)
from omniagents.core.config.loader import load_agent_spec_from_yaml


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content))


def _stub_tool(name: str) -> Any:
    """Minimal stand-in for a FunctionTool — has .name and a settable
    needs_approval attribute, which is all the loader/builder consult."""

    class _StubTool:
        pass

    t = _StubTool()
    t.name = name
    t.needs_approval = False
    return t


# -- Capability parsing -------------------------------------------------------


def test_parse_capability_pure_yaml_resolves_tools():
    """Pure-YAML capability synthesizes a _YamlCapability with the resolved
    tools, instructions, and approvals attached."""
    registry = {"alpha": _stub_tool("alpha"), "beta": _stub_tool("beta")}
    cap = parse_capability_spec(
        {
            "name": "fs",
            "tools": ["alpha", "beta"],
            "instructions": "use alpha for X",
            "approvals": {"alpha": {"fn": "module.confirm"}},
        },
        tool_registry=registry,
    )
    assert cap.capability_name == "fs"
    assert [t.name for t in cap.tools()] == ["alpha", "beta"]
    assert cap.instructions_text == "use alpha for X"
    assert cap.approvals_spec == {"alpha": {"fn": "module.confirm"}}


def test_parse_capability_unknown_tool_errors():
    """Tool resolution is eager so typos surface at parse time, not at agent
    build time when the registry is no longer in scope."""
    with pytest.raises(ValueError, match="unknown tool 'nope'"):
        parse_capability_spec(
            {"name": "fs", "tools": ["nope"]},
            tool_registry={"alpha": _stub_tool("alpha")},
        )


def test_parse_capability_requires_name():
    with pytest.raises(ValueError, match="non-empty 'name'"):
        parse_capability_spec({"tools": ["alpha"]}, tool_registry={})


def test_parse_capability_requires_tools_or_agent_tools():
    with pytest.raises(ValueError, match="at least one of 'tools' or 'agent_tools'"):
        parse_capability_spec({"name": "fs"}, tool_registry={})


def test_discover_capabilities_in_dir(tmp_path):
    """File-based discovery walks *.yml/*.yaml under the directory and
    parses each as a capability spec."""
    cap_dir = tmp_path / "capabilities"
    _write(
        cap_dir / "filesystem.yml",
        """
        name: filesystem
        tools: [alpha]
        """,
    )
    _write(
        cap_dir / "shell.yaml",
        """
        name: shell
        tools: [beta]
        """,
    )
    registry = {"alpha": _stub_tool("alpha"), "beta": _stub_tool("beta")}
    discovered = discover_capabilities_in_dir(cap_dir, tool_registry=registry)
    assert sorted(discovered) == ["filesystem", "shell"]
    assert [t.name for t in discovered["filesystem"].tools()] == ["alpha"]


def test_discover_capabilities_duplicate_names_error(tmp_path):
    cap_dir = tmp_path / "capabilities"
    _write(
        cap_dir / "a.yml",
        """
        name: shared
        tools: [alpha]
        """,
    )
    _write(
        cap_dir / "b.yml",
        """
        name: shared
        tools: [alpha]
        """,
    )
    with pytest.raises(ValueError, match="Duplicate capability name 'shared'"):
        discover_capabilities_in_dir(
            cap_dir, tool_registry={"alpha": _stub_tool("alpha")}
        )


# -- Agent loader: capabilities: resolution ----------------------------------


def test_loader_resolves_capabilities_into_spec(tmp_path):
    """Acceptance criterion: agent referencing a capability ends up with the
    cap's tools on its resolved_capabilities list."""
    registry = {"alpha": _stub_tool("alpha"), "beta": _stub_tool("beta")}
    cap = parse_capability_spec(
        {"name": "filesystem", "tools": ["alpha", "beta"]},
        tool_registry=registry,
    )
    cap_registry = {"filesystem": cap}

    agent_path = tmp_path / "agent.yml"
    _write(
        agent_path,
        """
        name: TestAgent
        instructions: be a test
        capabilities: [filesystem]
        """,
    )

    spec = load_agent_spec_from_yaml(
        str(agent_path),
        capability_registry=cap_registry,
    )
    assert len(spec.resolved_capabilities) == 1
    assert spec.resolved_capabilities[0].capability_name == "filesystem"


def test_loader_capabilities_dict_form(tmp_path):
    """Dict-form `{name: shell}` is equivalent to the bare-string form."""
    registry = {"alpha": _stub_tool("alpha")}
    cap = parse_capability_spec(
        {"name": "filesystem", "tools": ["alpha"]}, tool_registry=registry
    )
    agent_path = tmp_path / "agent.yml"
    _write(
        agent_path,
        """
        name: TestAgent
        instructions: be a test
        capabilities:
          - name: filesystem
        """,
    )
    spec = load_agent_spec_from_yaml(
        str(agent_path), capability_registry={"filesystem": cap}
    )
    assert len(spec.resolved_capabilities) == 1


def test_loader_capabilities_overrides_approvals(tmp_path):
    """Per-agent override of approvals replaces the cap's defaults without
    mutating the shared registry instance."""
    registry = {"alpha": _stub_tool("alpha")}
    cap = parse_capability_spec(
        {
            "name": "filesystem",
            "tools": ["alpha"],
            "approvals": {"alpha": {"fn": "module.default"}},
        },
        tool_registry=registry,
    )
    agent_path = tmp_path / "agent.yml"
    _write(
        agent_path,
        """
        name: TestAgent
        instructions: be a test
        capabilities:
          - name: filesystem
            overrides:
              approvals: {}
        """,
    )
    spec = load_agent_spec_from_yaml(
        str(agent_path), capability_registry={"filesystem": cap}
    )
    # Override applied to the spec's copy
    assert spec.resolved_capabilities[0].approvals_spec == {}
    # Shared registry instance unchanged
    assert cap.approvals_spec == {"alpha": {"fn": "module.default"}}


def test_loader_capabilities_overrides_instructions_append(tmp_path):
    """Override instructions are *appended* to the cap's, not replaced."""
    registry = {"alpha": _stub_tool("alpha")}
    cap = parse_capability_spec(
        {
            "name": "filesystem",
            "tools": ["alpha"],
            "instructions": "base text",
        },
        tool_registry=registry,
    )
    agent_path = tmp_path / "agent.yml"
    _write(
        agent_path,
        """
        name: TestAgent
        instructions: be a test
        capabilities:
          - name: filesystem
            overrides:
              instructions: appended text
        """,
    )
    spec = load_agent_spec_from_yaml(
        str(agent_path), capability_registry={"filesystem": cap}
    )
    text = spec.resolved_capabilities[0].instructions_text
    assert "base text" in text
    assert "appended text" in text
    assert text.index("base text") < text.index("appended text")


def test_loader_tool_name_conflict_across_capabilities(tmp_path):
    """When two capabilities used by the same agent both list the same tool,
    the loader errors at load time rather than letting the model see a
    duplicate."""
    shared_tool = _stub_tool("apply_patch")
    registry = {"apply_patch": shared_tool}
    cap_fs = parse_capability_spec(
        {"name": "filesystem", "tools": ["apply_patch"]},
        tool_registry=registry,
    )
    cap_kit = parse_capability_spec(
        {"name": "coding_kit", "tools": ["apply_patch"]},
        tool_registry=registry,
    )
    agent_path = tmp_path / "agent.yml"
    _write(
        agent_path,
        """
        name: TestAgent
        instructions: be a test
        capabilities: [filesystem, coding_kit]
        """,
    )
    with pytest.raises(ValueError, match="Capability tool name conflict"):
        load_agent_spec_from_yaml(
            str(agent_path),
            capability_registry={"filesystem": cap_fs, "coding_kit": cap_kit},
        )


def test_loader_unknown_capability_warns(tmp_path, capsys):
    agent_path = tmp_path / "agent.yml"
    _write(
        agent_path,
        """
        name: TestAgent
        instructions: be a test
        capabilities: [nonexistent]
        """,
    )
    spec = load_agent_spec_from_yaml(
        str(agent_path),
        capability_registry={"filesystem": object()},
    )
    assert spec.resolved_capabilities == []
    out = capsys.readouterr().out
    assert "nonexistent" in out
    assert "filesystem" in out  # available list


def test_loader_capabilities_without_registry_warns(tmp_path, capsys):
    agent_path = tmp_path / "agent.yml"
    _write(
        agent_path,
        """
        name: TestAgent
        instructions: be a test
        capabilities: [filesystem]
        """,
    )
    spec = load_agent_spec_from_yaml(str(agent_path))  # no capability_registry
    assert spec.resolved_capabilities == []
    out = capsys.readouterr().out
    assert "filesystem" in out
    assert "capability_registry" in out


# -- Builder: tools merge + approvals --------------------------------------


@pytest.mark.asyncio
async def test_builder_merges_capability_tools_into_agent(tmp_path, monkeypatch):
    """End-to-end: agent referencing a capability gets the cap's tools on
    the constructed Agent's tool list, plus the cap's instructions appended
    to the system prompt."""
    from omniagents.core.agents.builder import _default_build_agent

    registry = {"alpha": _stub_tool("alpha"), "beta": _stub_tool("beta")}
    cap = parse_capability_spec(
        {
            "name": "filesystem",
            "tools": ["alpha", "beta"],
            "instructions": "filesystem rules",
        },
        tool_registry=registry,
    )

    agent_path = tmp_path / "agent.yml"
    _write(
        agent_path,
        """
        name: TestAgent
        instructions: agent base instructions
        capabilities: [filesystem]
        """,
    )
    spec = load_agent_spec_from_yaml(
        str(agent_path),
        capability_registry={"filesystem": cap},
    )

    captured: Dict[str, Any] = {}

    class _StubAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    # Agent is imported inside _default_build_agent (`from agents import Agent`),
    # so patch on the source module rather than the builder module.
    monkeypatch.setattr("agents.Agent", _StubAgent)

    await _default_build_agent({}, None, spec)

    # Capability tools made it onto the agent
    tool_names = [t.name for t in captured["tools"]]
    assert tool_names == ["alpha", "beta"]
    # Instructions are wrapped as a Jinja2-rendering callable; invoke to
    # check the composed text. Both fragments must appear and the agent's
    # own instructions must come first.
    rendered = await captured["instructions"](None, None)
    assert "agent base instructions" in rendered
    assert "filesystem rules" in rendered
    assert rendered.index("agent base") < rendered.index("filesystem rules")


@pytest.mark.asyncio
async def test_builder_attaches_capability_approvals(tmp_path, monkeypatch):
    """Capability approvals: {tool: {fn: dotted.path}} sets the tool's
    needs_approval to the resolved callable."""
    from omniagents.core.agents.builder import _default_build_agent

    # Define an approval predicate at module scope so dotted-path import works
    approval_called: Dict[str, Any] = {}

    async def _approval_predicate(ctx, args, call_id):  # noqa: ARG001
        approval_called["yes"] = True
        return True

    # Register on a real importable module so resolve_predicate's dotted-path
    # branch can find it.
    import sys
    import types as pytypes

    mod = pytypes.ModuleType("_phase0_test_approvals_module")
    mod.predicate = _approval_predicate
    sys.modules["_phase0_test_approvals_module"] = mod

    registry = {"alpha": _stub_tool("alpha")}
    cap = parse_capability_spec(
        {
            "name": "filesystem",
            "tools": ["alpha"],
            "approvals": {"alpha": {"fn": "_phase0_test_approvals_module.predicate"}},
        },
        tool_registry=registry,
    )

    agent_path = tmp_path / "agent.yml"
    _write(
        agent_path,
        """
        name: TestAgent
        instructions: x
        capabilities: [filesystem]
        """,
    )
    spec = load_agent_spec_from_yaml(
        str(agent_path),
        capability_registry={"filesystem": cap},
    )

    captured: Dict[str, Any] = {}

    class _StubAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    # Agent is imported inside _default_build_agent (`from agents import Agent`),
    # so patch on the source module rather than the builder module.
    monkeypatch.setattr("agents.Agent", _StubAgent)

    await _default_build_agent({}, None, spec)

    # The capability's approvals_spec attached to the matching tool
    alpha_tool = next(t for t in captured["tools"] if t.name == "alpha")
    assert alpha_tool.needs_approval is _approval_predicate


@pytest.mark.asyncio
async def test_builder_mixed_mode_tools_and_capabilities(tmp_path, monkeypatch):
    """`tools:` and `capabilities:` coexist on the same agent during transition."""
    from omniagents.core.agents.builder import _default_build_agent

    fs_alpha = _stub_tool("alpha")
    fs_beta = _stub_tool("beta")
    standalone = _stub_tool("standalone")
    tool_registry = {
        "alpha": fs_alpha,
        "beta": fs_beta,
        "standalone": standalone,
    }
    cap = parse_capability_spec(
        {"name": "filesystem", "tools": ["alpha", "beta"]},
        tool_registry=tool_registry,
    )

    agent_path = tmp_path / "agent.yml"
    _write(
        agent_path,
        """
        name: TestAgent
        instructions: x
        tools: [standalone]
        capabilities: [filesystem]
        """,
    )
    spec = load_agent_spec_from_yaml(
        str(agent_path),
        tool_registry=tool_registry,
        capability_registry={"filesystem": cap},
    )

    captured: Dict[str, Any] = {}

    class _StubAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    # Agent is imported inside _default_build_agent (`from agents import Agent`),
    # so patch on the source module rather than the builder module.
    monkeypatch.setattr("agents.Agent", _StubAgent)

    await _default_build_agent({}, None, spec)

    tool_names = [t.name for t in captured["tools"]]
    # `tools:` entries come first, then capability tools appended.
    assert "standalone" in tool_names
    assert "alpha" in tool_names
    assert "beta" in tool_names
