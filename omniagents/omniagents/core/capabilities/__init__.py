"""Capability packs — a YAML-declared bundle of tools, instructions, and approval policy.

A capability is an SDK ``Capability`` instance (or subclass) that an agent can
reference by name. Capabilities are defined once at project scope (inline in
``project.yml`` or as files under ``paths.capabilities/``) and assigned to
agents via a per-agent ``capabilities:`` list. The agent loader resolves the
list against the project's capability registry; the agent builder calls
``cap.tools()`` to merge each capability's tool list into ``agent.tools``,
appends any ``cap.instructions(None)`` to the system prompt, and feeds
capability-level approval rules into the existing per-tool ``needs_approval``
machinery in :mod:`omniagents.core.agents.approval`.

Pure-YAML capabilities (those with no ``type:`` field) are synthesized at load
time into a default :class:`_YamlCapability`. Python-class capabilities use
``type: <name>`` to look up a registered subclass via
:func:`register_capability` (or the ``omniagents.capabilities`` entry point
group). Pre-registered builtins are added by Phase 1.
"""

from .registry import (
    register_capability,
    resolve_capability_class,
    list_registered_capabilities,
)
from .parser import (
    parse_capability_spec,
    discover_capabilities_in_dir,
)
from .yaml_capability import _YamlCapability

__all__ = [
    "register_capability",
    "resolve_capability_class",
    "list_registered_capabilities",
    "parse_capability_spec",
    "discover_capabilities_in_dir",
    "_YamlCapability",
]
