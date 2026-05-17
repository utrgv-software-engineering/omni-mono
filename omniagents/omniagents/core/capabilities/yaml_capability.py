"""Default Capability subclass for pure-YAML capabilities.

A capability YAML entry without a ``type:`` field is synthesized into a
:class:`_YamlCapability` at parse time. The class holds the resolved tool
list, instructions fragment, and approval policy from the YAML; its
``tools()`` and ``instructions()`` methods return them at agent build time.

Pure-YAML capabilities don't override the SDK lifecycle hooks for
``process_manifest``, ``sampling_params``, or ``process_context`` — those
require code, so users who need them write a Python ``Capability`` subclass
and register it via :func:`omniagents.core.capabilities.register_capability`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from agents.sandbox.capabilities.capability import Capability


class _YamlCapability(Capability):
    """Capability synthesized from a pure-YAML capability entry.

    The ``type:`` discriminator on the SDK base class is set to ``"yaml"`` so
    multiple synthesized capabilities can coexist without colliding on the
    registered-type lookup.
    """

    type: Literal["yaml"] = "yaml"

    capability_name: str = Field(default="", exclude=True)
    """The user-facing name of this capability (the YAML ``name:`` field).
    Stored separately from ``type`` so error messages can reference the
    declarative name rather than the synthesized discriminator."""

    resolved_tools: List[Any] = Field(default_factory=list, exclude=True)
    """Tool instances resolved from the YAML ``tools:`` list against the
    project tool registry. Populated at parse time so ``tools()`` is a
    cheap lookup."""

    agent_tool_configs: List[Any] = Field(default_factory=list, exclude=True)
    """``AgentToolConfig`` instances resolved from the YAML ``agent_tools:``
    list. The agent loader appends these to the consuming agent's
    ``agent_tool_configs`` so the builder's existing
    ``_build_agent_tools_from_configs`` path handles them uniformly."""

    instructions_text: Optional[str] = Field(default=None, exclude=True)
    """Instructions fragment from the YAML ``instructions:`` field, appended
    to the system prompt by the agent builder."""

    approvals_spec: Dict[str, Any] = Field(default_factory=dict, exclude=True)
    """Per-tool approval policy from the YAML ``approvals:`` block. Compiled
    to per-tool ``needs_approval`` predicates by the builder; unused at
    runtime by the capability itself."""

    sampling_params_override: Dict[str, Any] = Field(default_factory=dict, exclude=True)
    """Per-capability sampling parameter overrides from the YAML
    ``sampling_params:`` block."""

    def tools(self) -> List[Any]:
        return list(self.resolved_tools)

    async def instructions(self, manifest: Any = None) -> Optional[str]:
        # Manifest is sandbox-only; pure-YAML capabilities ignore it.
        _ = manifest
        return self.instructions_text or None

    def sampling_params(self, sampling_params: Dict[str, Any]) -> Dict[str, Any]:
        # Return only the cap-specific overrides; SDK runtime merges with the
        # caller's existing sampling params.
        return dict(self.sampling_params_override)
