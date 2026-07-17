"""Parse YAML capability declarations into Capability instances.

Capabilities are declared in two places:

* Inline in ``project.yml`` under a top-level ``capabilities:`` list.
* As individual YAML files under ``paths.capabilities/``.

Both produce the same shape — a list of dicts. Each dict has ``name``,
optional ``type``, optional ``tools``/``agent_tools``, optional
``instructions``/``approvals``/``options``/``sampling_params``. This module
parses each dict into a Capability instance, resolving tool references
against the project tool registry.

The parser is intentionally narrow: it produces Capability instances and
nothing else. Wiring (storing on ProjectContext, threading into the agent
loader) lives in :mod:`omniagents.core.project.runtime`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .registry import resolve_capability_class, list_registered_capabilities
from .yaml_capability import _YamlCapability

logger = logging.getLogger(__name__)


def _resolve_tool(name: str, tool_registry: Optional[Dict[str, Any]]) -> Any:
    """Look up a tool by name in the project registry. Raises ``ValueError``
    with an actionable message when the tool is missing."""
    if tool_registry is None:
        raise ValueError(
            f"Cannot resolve tool {name!r}: no tool_registry was provided to the "
            "capability parser. Capabilities must be parsed in a project context "
            "where tools have been discovered."
        )
    tool = tool_registry.get(name)
    if tool is None:
        available = sorted(tool_registry.keys())
        raise ValueError(
            f"Capability references unknown tool {name!r}. "
            f"Available tools ({len(available)}): {available[:20]}"
            + ("..." if len(available) > 20 else "")
        )
    return tool


def _resolve_server_function(
    name: str, server_functions_registry: Optional[Dict[str, Any]]
) -> Any:
    """Look up a server function by registered name. Raises ``ValueError``
    with an actionable message when it is missing — same fail-fast contract
    as ``_resolve_tool``."""
    if server_functions_registry is None:
        raise ValueError(
            f"Cannot resolve server function {name!r}: no "
            "server_functions_registry was provided to the capability parser. "
            "Capabilities must be parsed in a project context where server "
            "functions have been discovered."
        )
    meta = server_functions_registry.get(name)
    if meta is None:
        available = sorted(server_functions_registry.keys())
        raise ValueError(
            f"Capability references unknown server function {name!r}. "
            f"Available server functions ({len(available)}): {available[:20]}"
            + ("..." if len(available) > 20 else "")
        )
    return meta


def _resolve_agent_tool_entry(
    entry: Any,
    *,
    agents_map: Optional[Dict[str, Any]],
    cap_name: str,
    source: str,
) -> Optional[Any]:
    """Translate one capability ``agent_tools:`` YAML entry into an
    ``AgentToolConfig``. Capability agent_tools currently support the
    name-based ``agent: <name>`` form (and the bare-string shorthand) only;
    ``yaml:`` paths and ``inline:`` definitions are intentionally not
    supported here — capability YAMLs live under ``paths.capabilities/`` so
    relative paths would be ambiguous, and inline subagents inside a
    capability are an anti-pattern (define them as proper agents instead).
    """
    from omniagents.core.agents.specs import AgentToolConfig

    if isinstance(entry, str):
        entry = {"agent": entry}
    if not isinstance(entry, dict):
        raise ValueError(
            f"Capability {cap_name!r} agent_tools entry at {source} must be a "
            f"string or dict, got {type(entry).__name__}"
        )
    agent_name = entry.get("agent")
    if not isinstance(agent_name, str) or not agent_name.strip():
        raise ValueError(
            f"Capability {cap_name!r} agent_tools entry at {source} requires "
            "an 'agent' name (yaml: and inline: are not supported in capabilities)"
        )
    if agents_map is None:
        raise ValueError(
            f"Capability {cap_name!r} references agent_tools agent {agent_name!r} "
            "but no agents_map was provided to the capability parser. Capabilities "
            "with agent_tools must be parsed in a project context."
        )
    yaml_path = agents_map.get(agent_name)
    if yaml_path is None:
        raise ValueError(
            f"Capability {cap_name!r} agent_tools agent {agent_name!r} not found "
            f"in project. Available: {sorted(agents_map.keys())}"
        )
    return AgentToolConfig(
        yaml_path=str(yaml_path),
        tool_name=entry.get("tool_name"),
        tool_description=entry.get("tool_description"),
        is_enabled=entry.get("is_enabled"),
        needs_approval=entry.get("needs_approval"),
        max_turns=entry.get("max_turns"),
    )


def parse_capability_spec(
    spec: Dict[str, Any],
    *,
    tool_registry: Optional[Dict[str, Any]] = None,
    agents_map: Optional[Dict[str, Any]] = None,
    server_functions_registry: Optional[Dict[str, Any]] = None,
    capability_registry: Optional[Dict[str, Any]] = None,
    source: str = "<inline>",
) -> Any:
    """Parse a single capability YAML dict into a Capability instance.

    *spec* is the raw dict from YAML (as it appears in ``project.yml``'s
    ``capabilities:`` list, or the body of a file under ``paths.capabilities/``).
    *source* is a human-readable origin used in error messages.

    Subsetting: ``extends: <name>`` starts from an already-registered
    pure-YAML capability (typically the builtin being overridden) instead
    of a blank slate — the usual reason to override a builtin pack is to
    tweak one field, not to re-declare all of them. Merge semantics:

    - ``exclude_tools:`` / ``exclude_server_functions:`` remove entries
      inherited from the base (unknown names error — typo protection).
    - ``tools:`` / ``agent_tools:`` / ``server_functions:`` in the child
      are APPENDED to what remains inherited (deduped by name).
    - ``instructions:`` replaces the base's when present, else inherited.
    - ``instructions_append:`` appends to the base's instructions instead
      of replacing them (mutually exclusive with ``instructions:``) — for
      overrides that only add guidance, so the base text can't drift.
    - ``approvals:`` and ``sampling_params:`` merge child-over-base by key.

    Only pure-YAML capabilities can be extended, and ``extends`` cannot be
    combined with ``type:``. Bases resolve against *capability_registry*
    (the packs known when this spec is parsed — builtins when parsing
    project packs).

    Returns a Capability instance. Raises ``ValueError`` for missing/invalid
    fields so the caller can decide whether to abort discovery or skip.
    """
    if not isinstance(spec, dict):
        raise ValueError(
            f"Capability spec at {source} must be a dict, got {type(spec).__name__}"
        )

    name = spec.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(
            f"Capability at {source} requires a non-empty 'name' field"
        )

    tools_list = spec.get("tools") or []
    agent_tools_list = spec.get("agent_tools") or []
    server_functions_list = spec.get("server_functions") or []
    cap_type = spec.get("type")

    extends_name = spec.get("extends")
    base_cap: Optional[_YamlCapability] = None
    if extends_name is not None:
        if not isinstance(extends_name, str) or not extends_name.strip():
            raise ValueError(
                f"Capability {name!r} 'extends' must be a non-empty string"
            )
        if cap_type is not None:
            raise ValueError(
                f"Capability {name!r} at {source} cannot combine 'extends' "
                "with 'type:' — a Python-class capability owns its own surface"
            )
        if capability_registry is None:
            raise ValueError(
                f"Cannot resolve extends {extends_name!r} for capability "
                f"{name!r}: no capability_registry was provided to the parser"
            )
        base_cap = capability_registry.get(extends_name)
        if base_cap is None:
            available = sorted(capability_registry.keys())
            raise ValueError(
                f"Capability {name!r} extends unknown capability "
                f"{extends_name!r}. Known at this point: {available}"
            )
        if not isinstance(base_cap, _YamlCapability):
            raise ValueError(
                f"Capability {name!r} extends {extends_name!r}, which is a "
                "Python-class capability — only pure-YAML capabilities can "
                "be extended"
            )
    else:
        for key in ("exclude_tools", "exclude_server_functions"):
            if spec.get(key):
                raise ValueError(
                    f"Capability {name!r} uses {key!r} without 'extends:' — "
                    "there is nothing to exclude from"
                )

    # Pure-YAML capabilities must declare their surface explicitly.
    # Python-class capabilities (`type: <name>`) are exempt — the class owns
    # its tool construction (e.g. SDK Filesystem brings apply_patch/view_image
    # via its own tools() implementation, no YAML list needed). A commands-only
    # pack (server_functions without tools) is legal, as is an extends-only
    # pack (it inherits the base's surface).
    if (
        cap_type is None
        and base_cap is None
        and not tools_list
        and not agent_tools_list
        and not server_functions_list
    ):
        raise ValueError(
            f"Capability {name!r} at {source} must declare at least one of "
            "'tools', 'agent_tools', or 'server_functions' (or use 'type:' to "
            "reference a registered Capability class that owns its tool "
            "construction, or 'extends:' to subset an existing capability)"
        )
    if not isinstance(tools_list, list):
        raise ValueError(
            f"Capability {name!r} 'tools' must be a list, "
            f"got {type(tools_list).__name__}"
        )

    # Resolve tool names against the project tool registry now so capability
    # construction fails fast on typos.
    resolved_tools: List[Any] = []
    for tool_name in tools_list:
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError(
                f"Capability {name!r} 'tools' entries must be non-empty strings; "
                f"got {tool_name!r}"
            )
        resolved_tools.append(_resolve_tool(tool_name, tool_registry))

    # Resolve agent_tools entries (subagent-as-tool refs) into AgentToolConfig
    # instances. Empty when no agent_tools are declared on this cap.
    if not isinstance(agent_tools_list, list):
        raise ValueError(
            f"Capability {name!r} 'agent_tools' must be a list, "
            f"got {type(agent_tools_list).__name__}"
        )
    resolved_agent_tools: List[Any] = []
    for entry in agent_tools_list:
        cfg = _resolve_agent_tool_entry(
            entry, agents_map=agents_map, cap_name=name, source=source
        )
        if cfg is not None:
            resolved_agent_tools.append(cfg)

    # Resolve server-function names against the project registry now, same
    # fail-fast contract as tools:. Each resolved entry is the registry's
    # meta dict (func/description/schemas/on_invoke/strict) keyed by the
    # registered name — the service registers these when an agent lists
    # this capability.
    if not isinstance(server_functions_list, list):
        raise ValueError(
            f"Capability {name!r} 'server_functions' must be a list, "
            f"got {type(server_functions_list).__name__}"
        )
    resolved_server_functions: List[Dict[str, Any]] = []
    for sf_name in server_functions_list:
        if not isinstance(sf_name, str) or not sf_name.strip():
            raise ValueError(
                f"Capability {name!r} 'server_functions' entries must be "
                f"non-empty strings; got {sf_name!r}"
            )
        meta = _resolve_server_function(sf_name, server_functions_registry)
        entry = dict(meta)
        entry.setdefault("name", sf_name)
        resolved_server_functions.append(entry)

    instructions_text = spec.get("instructions")
    if instructions_text is not None and not isinstance(instructions_text, str):
        raise ValueError(
            f"Capability {name!r} 'instructions' must be a string, "
            f"got {type(instructions_text).__name__}"
        )

    instructions_append = spec.get("instructions_append")
    if instructions_append is not None and not isinstance(instructions_append, str):
        raise ValueError(
            f"Capability {name!r} 'instructions_append' must be a string, "
            f"got {type(instructions_append).__name__}"
        )
    if instructions_append is not None and instructions_text is not None:
        raise ValueError(
            f"Capability {name!r} at {source} cannot combine 'instructions' "
            "with 'instructions_append' — replace the base text or append "
            "to it, not both"
        )
    if instructions_append is not None and base_cap is None:
        raise ValueError(
            f"Capability {name!r} uses 'instructions_append' without "
            "'extends:' — there is no base text to append to"
        )

    approvals_spec = spec.get("approvals") or {}
    if not isinstance(approvals_spec, dict):
        raise ValueError(
            f"Capability {name!r} 'approvals' must be a dict, "
            f"got {type(approvals_spec).__name__}"
        )

    sampling_params_override = spec.get("sampling_params") or {}
    if not isinstance(sampling_params_override, dict):
        raise ValueError(
            f"Capability {name!r} 'sampling_params' must be a dict, "
            f"got {type(sampling_params_override).__name__}"
        )

    # Python-class capability: type: <name> resolves via the class registry.
    if cap_type is not None:
        cls = resolve_capability_class(cap_type)
        if cls is None:
            raise ValueError(
                f"Capability {name!r} type {cap_type!r} is not registered. "
                f"Available types: {list_registered_capabilities()}. "
                "Register via omniagents.core.capabilities.register_capability "
                "or the 'omniagents.capabilities' entry point group."
            )
        options = spec.get("options") or {}
        if not isinstance(options, dict):
            raise ValueError(
                f"Capability {name!r} 'options' must be a dict, "
                f"got {type(options).__name__}"
            )
        # Python-class capabilities own their tool construction; the YAML
        # tools: list is forwarded as a kwarg so the class can decide
        # whether to use it. The class is also responsible for instructions
        # and approvals (its instructions() method runs; its approvals are
        # stashed on the instance for the builder to read).
        try:
            instance = cls(**options)
        except Exception as e:
            raise ValueError(
                f"Failed to construct capability {name!r} (type {cap_type!r}): {e}"
            ) from e
        # Stash declarative metadata on the instance via object.__setattr__
        # — SDK Capability subclasses are strict Pydantic models that reject
        # ad-hoc field assignment through normal setattr. Builder code reads
        # these via getattr(cap, ..., default) so missing values are fine.
        for field_name, value in (
            ("capability_name", name),
            ("resolved_tools", resolved_tools),
            ("agent_tool_configs", resolved_agent_tools),
            ("instructions_text", instructions_text),
            ("approvals_spec", approvals_spec),
            ("server_function_specs", resolved_server_functions),
        ):
            object.__setattr__(instance, field_name, value)
        return instance

    # Merge over the extends base, when present (see docstring for rules).
    if base_cap is not None:
        exclude_tools = set(spec.get("exclude_tools") or [])
        exclude_sfs = set(spec.get("exclude_server_functions") or [])

        base_tool_names = {
            getattr(t, "name", None) for t in base_cap.resolved_tools
        }
        unknown_tools = exclude_tools - base_tool_names
        if unknown_tools:
            raise ValueError(
                f"Capability {name!r} exclude_tools references tools not in "
                f"{extends_name!r}: {sorted(unknown_tools)} "
                f"(base has: {sorted(n for n in base_tool_names if n)})"
            )
        base_sf_names = {m.get("name") for m in base_cap.server_function_specs}
        unknown_sfs = exclude_sfs - base_sf_names
        if unknown_sfs:
            raise ValueError(
                f"Capability {name!r} exclude_server_functions references "
                f"entries not in {extends_name!r}: {sorted(unknown_sfs)} "
                f"(base has: {sorted(n for n in base_sf_names if n)})"
            )

        inherited_tools = [
            t
            for t in base_cap.resolved_tools
            if getattr(t, "name", None) not in exclude_tools
        ]
        inherited_names = {getattr(t, "name", None) for t in inherited_tools}
        resolved_tools = inherited_tools + [
            t for t in resolved_tools if getattr(t, "name", None) not in inherited_names
        ]

        inherited_sfs = [
            m
            for m in base_cap.server_function_specs
            if m.get("name") not in exclude_sfs
        ]
        inherited_sf_names = {m.get("name") for m in inherited_sfs}
        resolved_server_functions = inherited_sfs + [
            m
            for m in resolved_server_functions
            if m.get("name") not in inherited_sf_names
        ]

        resolved_agent_tools = list(base_cap.agent_tool_configs) + resolved_agent_tools

        if instructions_append is not None:
            base_text = (base_cap.instructions_text or "").rstrip("\n")
            extra = instructions_append.strip("\n")
            instructions_text = f"{base_text}\n{extra}\n" if base_text else f"{extra}\n"
        elif instructions_text is None:
            instructions_text = base_cap.instructions_text
        approvals_spec = {**base_cap.approvals_spec, **approvals_spec}
        sampling_params_override = {
            **base_cap.sampling_params_override,
            **sampling_params_override,
        }

    # Pure-YAML capability: synthesize a _YamlCapability instance.
    return _YamlCapability(
        capability_name=name,
        resolved_tools=resolved_tools,
        agent_tool_configs=resolved_agent_tools,
        instructions_text=instructions_text,
        approvals_spec=approvals_spec,
        sampling_params_override=sampling_params_override,
        server_function_specs=resolved_server_functions,
    )


def discover_capabilities_in_dir(
    base_dir: str | Path,
    *,
    tool_registry: Optional[Dict[str, Any]] = None,
    agents_map: Optional[Dict[str, Any]] = None,
    server_functions_registry: Optional[Dict[str, Any]] = None,
    capability_registry: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Walk *base_dir* for ``*.yml`` / ``*.yaml`` files; parse each as a
    capability spec. Returns a name → Capability instance map.

    *capability_registry* seeds ``extends:`` resolution (e.g. builtins when
    discovering a project's packs). Packs parsed earlier in this directory
    are also visible to later files (files parse in sorted-name order, so
    same-dir extends is ordering-sensitive — prefer extending across
    layers, not within one directory).

    Errors during a single file's parse are logged and the file is skipped
    so one broken capability doesn't disable the whole project. Duplicate
    capability names across files raise ``ValueError`` — silent dedup hides
    modeling mistakes.
    """
    base_path = Path(base_dir).resolve()
    if not base_path.is_dir():
        return {}

    out: Dict[str, Any] = {}
    for yaml_file in sorted(base_path.glob("*.yml")) + sorted(base_path.glob("*.yaml")):
        try:
            with yaml_file.open("r", encoding="utf-8") as f:
                spec = yaml.safe_load(f)
        except Exception as e:
            logger.warning("Failed to read capability YAML %s: %s. Skipping.", yaml_file, e)
            continue
        if spec is None:
            continue
        try:
            cap = parse_capability_spec(
                spec,
                tool_registry=tool_registry,
                agents_map=agents_map,
                server_functions_registry=server_functions_registry,
                capability_registry={**(capability_registry or {}), **out},
                source=str(yaml_file),
            )
        except ValueError as e:
            logger.warning("Failed to parse capability %s: %s. Skipping.", yaml_file, e)
            continue
        cap_name = getattr(cap, "capability_name", None) or spec.get("name")
        if cap_name in out:
            raise ValueError(
                f"Duplicate capability name {cap_name!r} in {yaml_file} "
                f"(also defined elsewhere under {base_path})"
            )
        out[cap_name] = cap
    return out
