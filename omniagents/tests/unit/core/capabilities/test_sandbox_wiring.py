"""Sandbox framework wiring: capability packs flow into SandboxAgent
construction, and the host-mode merge is suppressed when sandbox is on."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any, Dict

import sys

import pytest

from omniagents.core.capabilities import (
    parse_capability_spec,
    resolve_capability_class,
    list_registered_capabilities,
)
from omniagents.core.config.loader import load_agent_spec_from_yaml


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content))


def _stub_tool(name: str) -> Any:
    class _StubTool:
        pass

    t = _StubTool()
    t.name = name
    t.needs_approval = False
    return t


# -- Registry --------------------------------------------------------------


def test_sdk_capabilities_pre_registered():
    """sdk_* names resolve to the SDK Capability subclasses without any
    explicit register_capability call."""
    registered = list_registered_capabilities()
    for name in (
        "sdk_filesystem",
        "sdk_shell",
        "sdk_skills",
        "sdk_memory",
        "sdk_compaction",
    ):
        assert name in registered, f"{name} should be auto-registered"

    fs_cls = resolve_capability_class("sdk_filesystem")
    from agents.sandbox.capabilities import Filesystem as SdkFilesystem

    assert fs_cls is SdkFilesystem


def test_parse_capability_with_sdk_type(tmp_path):
    """Capability YAML with `type: sdk_filesystem` constructs an instance of
    the SDK Filesystem class. tools: list isn't required because the SDK
    cap brings its own."""
    cap = parse_capability_spec(
        {"name": "fs_sandbox", "type": "sdk_filesystem", "tools": []},
        tool_registry={},
    )
    from agents.sandbox.capabilities import Filesystem as SdkFilesystem

    assert isinstance(cap, SdkFilesystem)
    assert cap.capability_name == "fs_sandbox"


# -- Builder: sandbox-mode skip + SandboxAgent capabilities= ----------------


@pytest.mark.asyncio
async def test_sandbox_mode_passes_caps_to_sandbox_agent(tmp_path, monkeypatch):
    """When use_sandbox_agent=true, resolved_capabilities flow into
    SandboxAgent(capabilities=...) AND the host-mode cap.tools() merge is
    skipped — SDK runtime owns the lifecycle in sandbox mode."""
    from omniagents.core.agents.builder import _default_build_agent

    # A pure-YAML cap with one stand-in tool. In host mode this would be
    # merged into agent.tools; in sandbox mode it goes via capabilities=.
    registry = {"alpha": _stub_tool("alpha")}
    cap = parse_capability_spec(
        {"name": "filesystem", "tools": ["alpha"], "instructions": "host fs rules"},
        tool_registry=registry,
    )

    agent_path = tmp_path / "agent.yml"
    _write(
        agent_path,
        """
        name: TestAgent
        instructions: agent base
        capabilities: [filesystem]
        sandbox:
          use_sandbox_agent: true
          client:
            type: unix_local
        """,
    )
    spec = load_agent_spec_from_yaml(
        str(agent_path),
        capability_registry={"filesystem": cap},
    )

    captured: Dict[str, Any] = {}

    class _StubSandboxAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    # The builder imports SandboxAgent from agents.sandbox at function scope.
    monkeypatch.setattr("agents.sandbox.SandboxAgent", _StubSandboxAgent)

    await _default_build_agent({}, None, spec)

    # Sandbox path was taken — the capabilities kwarg holds our cap
    assert "capabilities" in captured
    assert cap in captured["capabilities"]

    # Host-mode merge skipped — agent.tools does NOT include the cap's
    # alpha tool (SDK runtime will add it via capability.tools() at run prep)
    tool_names = [t.name for t in captured.get("tools", [])]
    assert "alpha" not in tool_names

    # Instructions don't have the cap fragment appended either — the SDK
    # runtime appends those during sandbox prep. Render the wrapped
    # callable and confirm only the agent's own text is present.
    rendered = await captured["instructions"](None, None)
    assert "agent base" in rendered
    assert "host fs rules" not in rendered


@pytest.mark.asyncio
async def test_non_sandbox_mode_still_merges_caps_host_side(tmp_path, monkeypatch):
    """Negative control — without sandbox.use_sandbox_agent, the existing
    Phase 0 host-mode behavior is preserved (cap.tools() merged into
    agent.tools, instructions appended)."""
    from omniagents.core.agents.builder import _default_build_agent

    registry = {"alpha": _stub_tool("alpha")}
    cap = parse_capability_spec(
        {"name": "filesystem", "tools": ["alpha"], "instructions": "host fs rules"},
        tool_registry=registry,
    )

    agent_path = tmp_path / "agent.yml"
    _write(
        agent_path,
        """
        name: TestAgent
        instructions: agent base
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
            self.tools = kwargs.get("tools", [])

        # install_output_budget wraps the built agent's tool resolution.
        async def get_all_tools(self, run_context):
            return list(self.tools)

    monkeypatch.setattr("agents.Agent", _StubAgent)
    await _default_build_agent({}, None, spec)

    tool_names = [t.name for t in captured["tools"]]
    assert "alpha" in tool_names  # merged host-side
    rendered = await captured["instructions"](None, None)
    assert "host fs rules" in rendered  # appended


@pytest.mark.skipif(
    sys.platform == "win32", reason="the unix_local sandbox is POSIX-only"
)
@pytest.mark.asyncio
async def test_sandbox_mode_combines_legacy_and_pack_caps(tmp_path, monkeypatch):
    """Legacy `sandbox.capabilities:` block and new `capabilities:` packs
    both flow into SandboxAgent's capabilities= so existing sandbox YAMLs
    keep working while new agent specs use the pack system."""
    from omniagents.core.agents.builder import _default_build_agent

    registry = {"alpha": _stub_tool("alpha")}
    cap = parse_capability_spec(
        {"name": "filesystem", "tools": ["alpha"]}, tool_registry=registry
    )

    agent_path = tmp_path / "agent.yml"
    _write(
        agent_path,
        """
        name: TestAgent
        instructions: x
        capabilities: [filesystem]
        sandbox:
          use_sandbox_agent: true
          client:
            type: unix_local
          capabilities:
            - shell    # legacy block uses unprefixed SDK class names
        """,
    )
    spec = load_agent_spec_from_yaml(
        str(agent_path),
        capability_registry={"filesystem": cap},
    )

    captured: Dict[str, Any] = {}

    class _StubSandboxAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("agents.sandbox.SandboxAgent", _StubSandboxAgent)
    await _default_build_agent({}, None, spec)

    cap_names = [
        getattr(c, "capability_name", None) or getattr(c, "type", "?")
        for c in captured.get("capabilities", [])
    ]
    # Pack cap (filesystem) AND legacy sandbox cap (Shell from sdk_shell ref)
    # both present.
    assert "filesystem" in cap_names
    assert any("shell" in str(n).lower() for n in cap_names)
