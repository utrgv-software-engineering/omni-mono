from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple
import fnmatch
import re
import fnmatch

import yaml

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
    tracing_defaults: Optional[Dict[str, Any]]
    settings_defaults: List[Dict[str, Any]]
    ui_defaults: Dict[str, Any]
    tool_registry: Dict[str, Any]
    guardrails_registry: Dict[str, Dict[str, Any]]
    server_functions_registry: Dict[str, Any]


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


def load_project(project_path: str) -> ProjectContext:
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
    eval_dir = _resolve_many("evaluations", "evaluations")
    target_dir = _resolve_many("target", "target")
    paths: Dict[str, List[Path]] = {
        "tools": tools_dirs,
        "guardrails": guardrails_dirs,
        "server_functions": server_functions_dirs,
        "agents": agents_dir,
        "evaluations": eval_dir,
        "target": target_dir,
    }

    agents_section = proj.get("agents") or {}
    entry = agents_section.get("entrypoint")
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

    tracing_defaults = (
        proj.get("tracing") if isinstance(proj.get("tracing"), dict) else None
    )
    settings_defaults = (
        proj.get("settings") if isinstance(proj.get("settings"), list) else []
    )
    ui_defaults = {}

    tool_registry: Dict[str, Any] = {}

    core_root = Path(__file__).resolve().parents[2]
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
    for d in guardrails_dirs:
        if d.is_dir():
            try:
                di, do = discover_guardrails_in_dir(str(d))
                in_guard.update(di)
                out_guard.update(do)
            except Exception:
                pass
    guardrails_registry = {"input": in_guard, "output": out_guard}

    server_functions_registry: Dict[str, Any] = {}
    for d in server_functions_dirs:
        if d.is_dir():
            try:
                sf = discover_server_functions_in_dir(str(d))
                server_functions_registry.update(sf)
            except Exception:
                pass

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
        tracing_defaults=tracing_defaults,
        settings_defaults=settings_defaults,
        ui_defaults=ui_defaults,
        tool_registry=tool_registry,
        guardrails_registry=guardrails_registry,
        server_functions_registry=server_functions_registry,
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


def build_spec_from_project(project: ProjectContext, agent_key: Optional[str] = None):
    key, agent_yaml = resolve_agent(project, agent_key)
    pdefs = {
        "tracing": project.tracing_defaults or None,
        "settings": project.settings_defaults or [],
    }
    pdefs.update(project.ui_defaults or {})
    spec = load_agent_spec_from_yaml(
        str(agent_yaml),
        tool_registry=project.tool_registry,
        guardrails_registry=project.guardrails_registry,
        server_functions_registry=project.server_functions_registry,
        project_defaults=pdefs,
    )
    project_name = project.raw.get("name") if isinstance(project.raw, dict) else None
    spec.project_slug = _slugify(project_name, project.root.name)
    spec.agent_slug = _slugify(key, key)
    return spec
