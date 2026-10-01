"""Select product-declared tool/instruction alternatives for one session."""

from __future__ import annotations

import copy
from typing import Any


def spec_for_capability_profile(spec: Any, session: Any) -> Any:
    variables = getattr(session, "variables", None)
    profile = (
        variables.get("capability_profile") if isinstance(variables, dict) else None
    )
    if profile is None:
        return spec
    profiles = getattr(spec, "capability_profiles", {})
    # Nested utility agents inherit the session for model settings, but have
    # their own fixed capability surface unless they opt into profiles.
    if not profiles:
        return spec
    if (
        not isinstance(profile, str)
        or not isinstance(profiles, dict)
        or profile not in profiles
    ):
        raise ValueError(f"Unknown capability profile: {profile!r}")
    replacements = profiles[profile]
    if not isinstance(replacements, dict):
        raise ValueError(
            "Capability profile must map existing packs to replacement packs"
        )
    registry = getattr(spec, "_capability_registry", None) or {}
    original = list(spec.resolved_capabilities)
    names = {getattr(cap, "capability_name", None) for cap in original}
    if set(replacements) - names:
        raise ValueError("Capability profile replaces a pack the agent does not have")
    resolved = []
    for cap in original:
        name = getattr(cap, "capability_name", None)
        if name not in replacements:
            resolved.append(cap)
            continue
        target = replacements[name]
        if not isinstance(target, str) or target not in registry:
            raise ValueError(f"Unknown replacement capability: {target!r}")
        replacement = registry[target]
        # Control-plane functions and nested-agent configs are registered at
        # definition load time; profiles only swap tools and instructions.
        if getattr(cap, "server_function_specs", []) != getattr(
            replacement, "server_function_specs", []
        ) or getattr(cap, "agent_tool_configs", []) != getattr(
            replacement, "agent_tool_configs", []
        ):
            raise ValueError(
                "Capability profiles must preserve server functions and agent-tool configurations"
            )
        resolved.append(replacement)
    seen = set()
    for cap in resolved:
        for tool in cap.tools():
            if tool.name in seen:
                raise ValueError(f"Duplicate tool in capability profile: {tool.name}")
            seen.add(tool.name)
    selected = copy.copy(spec)
    selected.resolved_capabilities = resolved
    return selected
