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

    server_function_specs: List[Dict[str, Any]] = Field(
        default_factory=list, exclude=True
    )
    """Server-function meta entries resolved from the YAML
    ``server_functions:`` list against the project registry. The service
    registers these when an agent lists this capability (duck-typed via
    ``server_functions()`` — the SDK base class doesn't know about them)."""

    def tools(self) -> List[Any]:
        return list(self.resolved_tools)

    def clone(self) -> "_YamlCapability":
        """Return a run-local capability without cloning definition graphs.

        The SDK binds ``session`` and ``run_as`` on a per-run clone, so the
        capability object itself must be distinct.  The remaining fields on a
        YAML capability are immutable project-definition objects: resolved
        tools, agent-tool configs, approval policy, and control-plane server
        functions.  In particular, discovered server-function metadata may
        contain callable/object graphs with cycles.  The SDK base clone walks
        every ``__dict__`` recursively and therefore cannot safely clone that
        metadata.

        A shallow Pydantic copy gives the run its own capability state while
        preserving the project-owned definitions by reference.  Accessors
        return fresh lists/dicts where callers need mutable containers.
        """

        return self.model_copy(deep=False)

    def server_functions(self) -> List[Dict[str, Any]]:
        return list(self.server_function_specs)

    async def instructions(self, manifest: Any = None) -> Optional[str]:
        # Manifest is sandbox-only; pure-YAML capabilities ignore it.
        _ = manifest
        return self.instructions_text or None

    def sampling_params(self, sampling_params: Dict[str, Any]) -> Dict[str, Any]:
        # Return only the cap-specific overrides; SDK runtime merges with the
        # caller's existing sampling params.
        return dict(self.sampling_params_override)
