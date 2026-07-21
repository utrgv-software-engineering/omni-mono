from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from omniagents.core.agents.specs import MCPServerConfig
import fnmatch
import re
import fnmatch

import yaml

from omniagents.core.security import SecurityConfig
from omniagents.core.tools.discovery import discover_functions_in_dir
from omniagents.core.guardrails.discovery import discover_guardrails_in_dir
from omniagents.core.server_functions.discovery import discover_server_functions_in_dir
from omniagents.core.config.loader import load_agent_spec_from_yaml


def _read_yaml(path: Path) -> Dict[str, Any]:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _to_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [str(x) for x in v]
    return []


def _slugify(value: str | None, default: str) -> str:
    token = (value or "").strip().lower()
    if not token:
        token = default
    token = re.sub(r"[^a-z0-9\-_]+", "_", token)
    token = re.sub(r"_+", "_", token).strip("_")
    return token or default


@dataclass
class ProjectContext:
    root: Path
    raw: Dict[str, Any]
    paths: Dict[str, List[Path]]
    agents_map: Dict[str, Path]
    entrypoint: Optional[str]
    voice_entrypoint: Optional[str]
    tracing_defaults: Optional[Dict[str, Any]]
    settings_defaults: List[Dict[str, Any]]
    ui_defaults: Dict[str, Any]
    tool_registry: Dict[str, Any]
    guardrails_registry: Dict[str, Dict[str, Any]]
    server_functions_registry: Dict[str, Any]
    # Capability registry: name → resolved Capability instance.
    # Built from `paths.capabilities/*.yml` files plus inline `capabilities:`
    # block in project.yml. Empty dict when no capabilities are configured.
    capability_registry: Dict[str, Any] = field(default_factory=dict)
    # Names in server_functions_registry that came from the builtin dir
    # (omniagents/builtin/server_functions/). These are capability-gated:
    # excluded from the spec's auto-registrar and registered only when an
    # agent lists a capability naming them. Project server functions are
    # never in this set, even when they override a builtin name.
    builtin_server_function_names: set = field(default_factory=set)
    # User-supplied MCP server support
    allow_user_mcp_servers: bool = False
    user_mcp_servers: List["MCPServerConfig"] = field(default_factory=list)
    # Project-level security policy
    security: SecurityConfig = field(default_factory=SecurityConfig)


def _validate_agent_name(expected_key: str, yaml_name: Any, path: Path) -> str:
    if not isinstance(yaml_name, str) or not yaml_name.strip():
        raise ValueError(
            f"Agent YAML missing a valid 'name' (file: {path}) — set name: {expected_key}"
        )
    if yaml_name != expected_key:
        raise ValueError(
            f"Agent name must equal its directory key. Got name='{yaml_name}' file='{path.name}'. Expected name='{expected_key}'."
        )
    if len(yaml_name) > 64:
        raise ValueError(f"Agent name '{yaml_name}' too long (>64). File: {path}")
    if yaml_name.lower() != yaml_name:
        raise ValueError(f"Agent name '{yaml_name}' must be lowercase. File: {path}")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", yaml_name):
        raise ValueError(
            f"Agent name '{yaml_name}' invalid. Allowed: [a-z][a-z0-9_]*. File: {path}"
        )
    return yaml_name


def _project_overlay_paths(project_name: Any) -> List[Path]:
    """Overlay files layered over the base project.yml, in merge order.

    Two sources, mirroring the sandbox-profile chain:

    1. Config-dir convention: ``<product-config>/project.d/*.yml``
       (lexical order) — the user/operator layer for an installed
       product whose packaged base project.yml is read-only.
    2. ``OMNIAGENTS_PROJECT_OVERLAYS`` — ``os.pathsep``-separated
       explicit paths (e.g. a container image selecting its enterprise
       security overlay); applied last, so explicit wins.
    """
    import os

    out: List[Path] = []
    if isinstance(project_name, str) and project_name.strip():
        try:
            from omniagents.core.config.dirs import product_config_dir

            overlay_dir = product_config_dir(project_name.strip()) / "project.d"
            if overlay_dir.is_dir():
                out.extend(sorted(overlay_dir.glob("*.yml")))
        except Exception:
            pass

    env_overlays = os.environ.get("OMNIAGENTS_PROJECT_OVERLAYS", "")
    for chunk in env_overlays.split(os.pathsep):
        chunk = chunk.strip()
        if chunk:
            out.append(Path(chunk).expanduser())
    return out


def _apply_project_overlays(
    proj: Dict[str, Any], overlays: Optional[List[Path]] = None
) -> Tuple[Dict[str, Any], List[Path]]:
    """Merge overlay documents over the base project dict.

    Merge semantics are the framework's one layered-config rule (dicts
    merge recursively, lists and scalars replace) — identical to sandbox
    profiles. Returns the merged dict plus the overlay paths applied.
    """
    from omniagents.core.utils.merge import deep_merge

    applied: List[Path] = []
    candidates = list(_project_overlay_paths(proj.get("name")))
    candidates.extend(overlays or [])
    for overlay_path in candidates:
        if not overlay_path.is_file():
            continue
        overlay = _read_yaml(overlay_path)
        if not isinstance(overlay, dict) or not overlay:
            continue
        proj = deep_merge(proj, overlay)
        applied.append(overlay_path)
    return proj, applied


def load_project(
    project_path: str, *, overlays: Optional[List[Path]] = None
) -> ProjectContext:
    p = Path(project_path).resolve()
    if p.is_file() and p.name != "project.yml":
        raise FileNotFoundError(
            f"Expected 'project.yml' but got '{p.name}'. Rename to project.yml."
        )
    base_dir = p.parent if p.is_file() else p
    if not p.is_file():
        proj_file = base_dir / "project.yml"
        if not proj_file.is_file():
            raise FileNotFoundError(
                f"Could not find project.yml in directory: {base_dir}"
            )
    proj = _read_yaml(p if p.is_file() else (base_dir / "project.yml"))
    proj, _applied_overlays = _apply_project_overlays(proj, overlays)

    # Install the manifest's product identity (`product:` section) as the
    # process-wide default. A prior explicit
    # omniagents.core.product.set_product_info / bootstrap_product wins —
    # loading a project never clobbers an explicitly installed identity.
    if proj.get("product") is not None:
        from omniagents.core.product import (
            _set_default_product_info,
            product_info_from_mapping,
        )

        _set_default_product_info(product_info_from_mapping(proj["product"]))

    paths_cfg = proj.get("paths") or {}

    def _resolve_many(kind: str, default: Optional[str] = None) -> List[Path]:
        vals = _to_list(paths_cfg.get(kind, default))
        out: List[Path] = []
        for v in vals:
            pp = Path(v)
            if not pp.is_absolute():
                pp = (base_dir / v).resolve()
            out.append(pp)
        return out

    tools_dirs = _resolve_many("tools", "tools")
    guardrails_dirs = _resolve_many("guardrails", "guardrails")
    server_functions_dirs = _resolve_many("server_functions", "server_functions")
    agents_dir = _resolve_many("agents", "agents")
    capabilities_dirs = _resolve_many("capabilities", "capabilities")
    eval_dir = _resolve_many("evaluations", "evaluations")
    target_dir = _resolve_many("target", "target")
    # Optional shared instruction-fragment dirs for {% include %} in agent
    # instructions. No default: the file-relative `_shared/` conventions are
    # always active; this key only adds project-level fragment roots.
    instruction_fragments_dirs = _resolve_many("instruction_fragments")
    paths: Dict[str, List[Path]] = {
        "tools": tools_dirs,
        "guardrails": guardrails_dirs,
        "server_functions": server_functions_dirs,
        "agents": agents_dir,
        "capabilities": capabilities_dirs,
        "evaluations": eval_dir,
        "target": target_dir,
        "instruction_fragments": instruction_fragments_dirs,
    }

    agents_section = proj.get("agents") or {}
    entry = agents_section.get("entrypoint")
    voice_entry = agents_section.get("voice_entrypoint")
    include = agents_section.get("include")
    exclude = agents_section.get("exclude")
    include_globs = (
        [str(x) for x in (include or [])]
        if isinstance(include, list)
        else ([] if include in (None, "") else [str(include)])
    )
    exclude_globs = (
        [str(x) for x in (exclude or [])]
        if isinstance(exclude, list)
        else ([] if exclude in (None, "") else [str(exclude)])
    )
    # Strict v2: discover per-entity agent.yml under agents/<key>/agent.yml
    candidates: List[Path] = []
    for adir in agents_dir:
        if adir.is_file():
            raise FileNotFoundError(
                f"Strict layout requires 'agents/<key>/agent.yml'. Got file instead of directory: {adir}"
            )
        if adir.is_dir():
            for y in sorted(list(adir.rglob("agent.yml"))):
                # only accept files with a directory parent key
                if y.parent and y.parent != adir:
                    candidates.append(y)
                else:
                    # allow agent.yml directly under agents/ as a single agent named by folder; discouraged but enforce strict by requiring subdir
                    continue
    agents_map: Dict[str, Path] = {}
    seen: set[str] = set()
    for y in candidates:
        try:
            data = _read_yaml(y)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        dir_key = y.parent.name
        declared = data.get("name")
        key = _validate_agent_name(dir_key, declared, y)
        if include_globs and not any(fnmatch.fnmatch(key, g) for g in include_globs):
            continue
        if exclude_globs and any(fnmatch.fnmatch(key, g) for g in exclude_globs):
            continue
        if key in seen:
            raise ValueError(
                f"Duplicate agent name/key '{key}'. Another folder already defines this agent. Current: {y}"
            )
        seen.add(key)
        agents_map[key] = y.resolve()
    if not agents_map:
        raise FileNotFoundError(
            "No agents discovered. Strict layout requires 'agents/<key>/agent.yml'."
        )
    if isinstance(entry, str) and entry:
        if entry not in agents_map:
            raise FileNotFoundError(
                f"Entrypoint '{entry}' not found among agents: {sorted(agents_map.keys())}"
            )
    else:
        if len(agents_map) == 1:
            entry = next(iter(agents_map.keys()))
        else:
            entry = None

    if isinstance(voice_entry, str) and voice_entry:
        if voice_entry not in agents_map:
            raise FileNotFoundError(
                f"Voice entrypoint '{voice_entry}' not found among agents: {sorted(agents_map.keys())}"
            )
    elif voice_entry in (None, ""):
        voice_entry = None
    else:
        raise ValueError("Voice entrypoint must be a string if provided")

    if voice_entry is None:
        voice_entry = entry

    core_root = Path(__file__).resolve().parents[2]

    # --- Builtin agents ---
    # The framework ships stock sub-agent specs under
    # omniagents/builtin/agents/<key>/agent.yml (e.g. the read-only
    # `explorer` wired up by the builtin `exploration` capability pack).
    # They merge into agents_map here — AFTER entrypoint/voice-entrypoint
    # resolution, so a single-agent project keeps its auto-entrypoint and
    # a project with no agents still fails loudly above. Precedence
    # mirrors builtin capabilities: a project agent with the same key
    # shadows the builtin entirely. The project's agents.include/exclude
    # globs apply, so a project can opt out (exclude: ["explorer"]).
    builtin_agents_dir = (core_root / "builtin" / "agents").resolve()
    if builtin_agents_dir.is_dir():
        for y in sorted(builtin_agents_dir.glob("*/agent.yml")):
            data = _read_yaml(y)
            if not isinstance(data, dict):
                continue
            try:
                key = _validate_agent_name(y.parent.name, data.get("name"), y)
            except ValueError as e:
                print(f"Warning: skipping builtin agent {y}: {e}")
                continue
            if key in agents_map:
                continue  # project definition shadows the builtin
            if include_globs and not any(
                fnmatch.fnmatch(key, g) for g in include_globs
            ):
                continue
            if exclude_globs and any(fnmatch.fnmatch(key, g) for g in exclude_globs):
                continue
            agents_map[key] = y.resolve()

    tracing_defaults = (
        proj.get("tracing") if isinstance(proj.get("tracing"), dict) else None
    )
    settings_defaults = (
        proj.get("settings") if isinstance(proj.get("settings"), list) else []
    )
    ui_defaults = {}

    tool_registry: Dict[str, Any] = {}

    builtin_tools_dir = (core_root / "builtin" / "tools").resolve()
    if builtin_tools_dir.is_dir():
        try:
            builtins_found = discover_functions_in_dir(str(builtin_tools_dir))
            tool_registry.update(builtins_found)
        except Exception:
            pass

    for d in tools_dirs:
        if d.is_dir():
            try:
                found = discover_functions_in_dir(str(d))
                tool_registry.update(found)
            except Exception:
                pass

    in_guard: Dict[str, Any] = {}
    out_guard: Dict[str, Any] = {}
    tool_in_guard: Dict[str, Any] = {}
    tool_out_guard: Dict[str, Any] = {}
    for d in guardrails_dirs:
        if d.is_dir():
            try:
                di, do, ti, to = discover_guardrails_in_dir(str(d))
                in_guard.update(di)
                out_guard.update(do)
                tool_in_guard.update(ti)
                tool_out_guard.update(to)
            except Exception:
                pass
    guardrails_registry = {
        "input": in_guard,
        "output": out_guard,
        "tool_input": tool_in_guard,
        "tool_output": tool_out_guard,
    }

    # Builtin server functions first, project dirs override by name —
    # same precedence as builtin tools and builtin capabilities. Builtin
    # server functions are NOT auto-registered on the service; they only
    # activate when a capability the agent lists names them (project
    # server functions keep their historical auto-registration).
    server_functions_registry: Dict[str, Any] = {}
    builtin_sf_dir = (core_root / "builtin" / "server_functions").resolve()
    builtin_server_function_names: set[str] = set()
    if builtin_sf_dir.is_dir():
        try:
            builtin_sf = discover_server_functions_in_dir(str(builtin_sf_dir))
            server_functions_registry.update(builtin_sf)
            builtin_server_function_names = set(builtin_sf.keys())
        except Exception as e:
            print(
                f"Warning: builtin server-function discovery failed: {e}. "
                "Builtin server functions unavailable."
            )
    for d in server_functions_dirs:
        if d.is_dir():
            try:
                sf = discover_server_functions_in_dir(str(d))
                server_functions_registry.update(sf)
                # A project definition takes over the name entirely —
                # including auto-registration.
                builtin_server_function_names -= set(sf.keys())
            except Exception:
                pass

    # --- Capability Discovery ---
    # Three sources, parsed with the same schema: omniagents builtin caps
    # (omniagents/builtin/capabilities/), inline `capabilities:` block in
    # project.yml, and YAML files under paths.capabilities/. Tool registry
    # must be built first because the parser resolves tool name → instance
    # eagerly, so capability-spec errors surface here rather than at agent
    # build time.
    #
    # Precedence: project-defined capabilities (inline or paths.capabilities/)
    # *override* builtins of the same name — letting users redefine the
    # standard bundles for their project. Two project-side definitions of
    # the same name are an error (silent dedup hides modeling mistakes).
    capability_registry: Dict[str, Any] = {}
    try:
        from omniagents.core.capabilities import (
            discover_capabilities_in_dir,
            parse_capability_spec,
        )

        # Builtin capabilities first; users can override below. agents_map
        # already includes the builtin agents at this point, so builtin
        # packs that expose a sub-agent via agent_tools (e.g. exploration's
        # `explore` → builtin explorer) resolve here — against the project's
        # shadowing definition when one exists.
        builtin_caps_dir = (core_root / "builtin" / "capabilities").resolve()
        if builtin_caps_dir.is_dir():
            try:
                builtin_caps = discover_capabilities_in_dir(
                    str(builtin_caps_dir),
                    tool_registry=tool_registry,
                    agents_map=agents_map,
                    server_functions_registry=server_functions_registry,
                    capability_registry=capability_registry,
                )
                capability_registry.update(builtin_caps)
            except Exception as e:
                print(
                    f"Warning: builtin capability discovery failed: {e}. "
                    "Builtin capabilities unavailable."
                )

        # Project-side capabilities. Track which names came from the project
        # so a duplicate between two project sources errors out, while a
        # project name silently overrides a builtin (intentional precedence).
        project_cap_names: set[str] = set()
        for cap_dir in capabilities_dirs:
            if cap_dir.is_dir():
                discovered = discover_capabilities_in_dir(
                    str(cap_dir),
                    tool_registry=tool_registry,
                    agents_map=agents_map,
                    server_functions_registry=server_functions_registry,
                    capability_registry=capability_registry,
                )
                for cap_name, cap in discovered.items():
                    if cap_name in project_cap_names:
                        raise ValueError(
                            f"Duplicate capability name {cap_name!r} in {cap_dir}"
                        )
                    project_cap_names.add(cap_name)
                    capability_registry[cap_name] = cap

        inline_caps = proj.get("capabilities")
        if inline_caps is not None:
            if not isinstance(inline_caps, list):
                print(
                    "Warning: project.yml 'capabilities' must be a list. Ignoring."
                )
            else:
                for cap_spec in inline_caps:
                    cap = parse_capability_spec(
                        cap_spec,
                        tool_registry=tool_registry,
                        agents_map=agents_map,
                        server_functions_registry=server_functions_registry,
                        capability_registry=capability_registry,
                        source="project.yml",
                    )
                    cap_name = getattr(cap, "capability_name", None) or cap_spec.get("name")
                    if cap_name in project_cap_names:
                        raise ValueError(
                            f"Duplicate capability name {cap_name!r}: defined inline "
                            f"in project.yml and also under paths.capabilities/"
                        )
                    project_cap_names.add(cap_name)
                    capability_registry[cap_name] = cap
    except ValueError:
        # Capability spec errors are user-facing — re-raise so the project
        # fails to load rather than silently dropping capabilities.
        raise
    except Exception as e:
        print(f"Warning: Capability discovery failed: {e}. Capabilities disabled.")
        capability_registry = {}

    # --- Security Configuration ---
    security = SecurityConfig.from_dict(proj.get("security"))

    # --- User MCP Server Support ---
    allow_user_mcp = proj.get("allow_user_mcp_servers", False)
    if not isinstance(allow_user_mcp, bool):
        print("Warning: 'allow_user_mcp_servers' should be a boolean. Using False.")
        allow_user_mcp = False

    # MCP allowlist overrides user MCP permission
    if security.mcp_require_allowlist and allow_user_mcp:
        print("Security: mcp_require_allowlist is enabled — disabling user MCP servers")
        allow_user_mcp = False

    user_mcp_configs: List[Any] = []
    if allow_user_mcp:
        from omniagents.core.config.user_mcp import load_user_mcp_config

        project_name = proj.get("name")
        if not project_name or not isinstance(project_name, str):
            project_name = base_dir.name
        project_name_slug = _slugify(project_name, base_dir.name)
        user_mcp_configs = load_user_mcp_config(
            project_root=base_dir,
            project_name=project_name_slug,
        )

    return ProjectContext(
        root=base_dir,
        raw=proj,
        paths=paths,
        agents_map=agents_map,
        entrypoint=(
            str(entry)
            if entry
            else (next(iter(agents_map.keys())) if agents_map else None)
        ),
        voice_entrypoint=(str(voice_entry) if voice_entry else None),
        tracing_defaults=tracing_defaults,
        settings_defaults=settings_defaults,
        ui_defaults=ui_defaults,
        tool_registry=tool_registry,
        guardrails_registry=guardrails_registry,
        server_functions_registry=server_functions_registry,
        capability_registry=capability_registry,
        builtin_server_function_names=builtin_server_function_names,
        allow_user_mcp_servers=allow_user_mcp,
        user_mcp_servers=user_mcp_configs,
        security=security,
    )


def resolve_agent(
    project: ProjectContext, agent_key: Optional[str] = None
) -> Tuple[str, Path]:
    key = agent_key or project.entrypoint
    if not key:
        raise FileNotFoundError("No agent entrypoint defined in project.yml")
    if key not in project.agents_map:
        raise FileNotFoundError(f"Agent key not found in project: {key}")
    return key, project.agents_map[key]


def build_spec_from_project(
    project: ProjectContext,
    agent_key: Optional[str] = None,
):
    raw_agents_config = (
        project.raw.get("agents") if isinstance(project.raw, dict) else {}
    )
    configured_voice_key = None
    if isinstance(raw_agents_config, dict):
        raw_voice = raw_agents_config.get("voice_entrypoint")
        if isinstance(raw_voice, str) and raw_voice.strip():
            configured_voice_key = raw_voice.strip()

    selected_key = agent_key
    key, agent_yaml = resolve_agent(project, selected_key)
    pdefs = {
        "tracing": project.tracing_defaults or None,
        "settings": project.settings_defaults or [],
        "security": project.security.to_dict(),
        # paths.instruction_fragments — extra {% include %} search roots
        "instruction_fragments": [
            str(p) for p in (project.paths.get("instruction_fragments") or [])
        ],
    }
    pdefs.update(project.ui_defaults or {})
    # Builtin server functions are capability-gated (registered by the
    # service when the agent lists the owning capability); only project
    # server functions go through the spec's auto-registrar.
    project_only_sf = {
        k: v
        for k, v in project.server_functions_registry.items()
        if k not in project.builtin_server_function_names
    }
    spec = load_agent_spec_from_yaml(
        str(agent_yaml),
        tool_registry=project.tool_registry,
        guardrails_registry=project.guardrails_registry,
        server_functions_registry=project_only_sf,
        agents_map=project.agents_map,
        capability_registry=project.capability_registry,
        project_defaults=pdefs,
        user_mcp_servers=(
            project.user_mcp_servers if project.allow_user_mcp_servers else None
        ),
    )
    project_name = project.raw.get("name") if isinstance(project.raw, dict) else None
    spec.project_slug = _slugify(project_name, project.root.name)
    spec.agent_slug = _slugify(key, key)

    voice_spec = None
    voice_key = configured_voice_key
    if voice_key:
        voice_path = project.agents_map.get(voice_key)
        if voice_path:
            voice_spec = load_agent_spec_from_yaml(
                str(voice_path),
                tool_registry=project.tool_registry,
                guardrails_registry=project.guardrails_registry,
                server_functions_registry=project_only_sf,
                agents_map=project.agents_map,
                capability_registry=project.capability_registry,
                project_defaults=pdefs,
                user_mcp_servers=(
                    project.user_mcp_servers if project.allow_user_mcp_servers else None
                ),
            )
            voice_spec.project_slug = spec.project_slug
            voice_spec.agent_slug = _slugify(voice_key, voice_key)
    if voice_spec:
        spec.voice_spec = voice_spec
    return spec
