"""Sandbox profile loading, layering, and substitution.

A sandbox profile is a YAML document that describes one runnable sandbox:
which backend client, which manifest, which long-running services to start.
It composes the SDK's ``Manifest`` and ``*ClientOptions`` with one
extension — ``services`` — for processes the launcher wants alive inside
the sandbox.

Schema (illustrative)::

    version: 1
    client:
      type: docker | unix_local | e2b | ...
    options:
      image: ghcr.io/...:tag
      exposed_ports: [8080, 6080]
    manifest:
      root: /workspace
      environment: { KEY: value }
      # entries: handled by the launcher's per-project source kind, NOT
      # by the profile. The profile describes the *container shape*;
      # the *workspace seeding* is a per-project concern.
    services:
      <name>:
        command: "argv string"          # required
        port: 8080                      # optional; resolved to a URL at runtime
        env: { KEY: value }             # optional
        user: "1000:1000"               # optional

Resolution order (last wins on key conflict, dicts merge, lists replace):

    1. omni-code built-in default (this package)
    2. <config>/sandbox/default.yml          (launcher writes from global settings)
    3. <config>/projects/<projectId>/sandbox.yml   (launcher writes per-project)
    4. --profile <path>                      (explicit CLI override; tests, scripting)

The only host-path interpolation supported is ``${workspace_dir}``,
substituted from ``omni serve --workspace <dir>`` before the profile is
handed to the SDK. Everything else host-specific (snapshot storage,
network allowlist) is passed via dedicated CLI flags / config files and
threaded into the manifest at runtime — keeps the profile mostly about
container shape.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import yaml

from omni_code.config import get_config_dir


# Env var the in-container `apply-network-isolation.sh` reads. Set on the
# manifest environment so the entrypoint init step can apply iptables
# allowlist rules. Auto-populated from ``<config>/network.json`` unless
# the profile sets it explicitly.
_NETWORK_ALLOWLIST_ENV = "OMNI_SANDBOX_NETWORK_ALLOWLIST"


_DEFAULT_PROFILE_PATH = Path(__file__).resolve().parent / "sandbox_profile_default.yml"


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    command: str
    port: Optional[int] = None
    env: Mapping[str, str] = field(default_factory=dict)
    user: Optional[str] = None


@dataclass(frozen=True)
class TerminalSpec:
    """Shape of an interactive shell spawned by the launcher's Terminal app.

    The launcher's main process calls ``terminal.create`` (an omni-code
    server function) which constructs the shell using these defaults plus
    any per-call overrides. The shell runs inside the sandbox via
    :class:`omniagents.rpc.terminal_backends.SessionPtyBackend` — same
    user/cwd/manifest as the agent's bash tool, just held open with a PTY.
    """

    command: str = "bash -i"
    user: Optional[str] = None
    cwd: Optional[str] = None


@dataclass(frozen=True)
class ResolvedProfile:
    """The merged, substituted profile, split into the layers ``omni serve`` needs."""

    raw: Dict[str, Any]
    """The full merged YAML dict. Pass ``raw`` (or subsets of it) to
    ``omniagents.core.sandbox.factory.build_sandbox_client`` /
    ``build_manifest`` to get SDK objects."""

    init: List[str]
    """Shell commands to run synchronously inside the freshly-started
    session, before any service is launched. Useful for one-shot setup
    that the SDK's container override has bypassed (user remap, daemon
    init, gitconfig persistence) — anything that the image's ENTRYPOINT
    would have done if it were allowed to run.

    Each entry is a single shell command. Run in order; first non-zero
    exit aborts the launch with the failing command in the error."""

    services: List[ServiceSpec]
    terminal: TerminalSpec
    """Defaults for ``terminal.create`` server-function calls. The launcher
    creates one of these per Terminal app tab; the per-call overrides
    layer on top of these defaults."""

    sources: List[Path]
    """Paths of the profile layers actually applied (for debugging / --output)."""


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_profile(
    *,
    workspace_dir: Path,
    project_id: Optional[str] = None,
    explicit_path: Optional[Path] = None,
) -> ResolvedProfile:
    """Resolve a profile through the layered chain.

    *workspace_dir* is the host directory the agent edits in-place; it
    replaces every ``${workspace_dir}`` occurrence in the merged YAML.
    Passed by ``omni serve --workspace <dir>``.

    When *explicit_path* is given, only the built-in default + the explicit
    file are merged — the user/project layers are skipped. This is the
    "tests, scripting, debugging" path; the launcher should not use it.
    """
    layers = _collect_layers(project_id=project_id, explicit_path=explicit_path)
    merged: Dict[str, Any] = {}
    for _src, data in layers:
        merged = _deep_merge(merged, data)

    merged = _substitute(merged, {"workspace_dir": str(workspace_dir.resolve())})
    _inject_network_allowlist(merged)
    init = _parse_init(merged.get("init"))
    services = _parse_services(merged.get("services") or {})
    terminal = _parse_terminal(merged.get("terminal"), run_as=merged.get("run_as"))
    _validate(merged, services)

    return ResolvedProfile(
        raw=merged,
        init=init,
        services=services,
        terminal=terminal,
        sources=[src for src, _ in layers],
    )


def _substitute(value: Any, mapping: Mapping[str, str]) -> Any:
    """Recursively replace ``${key}`` tokens in every string in *value*.

    Unknown ``${...}`` tokens are left as-is so misspellings stay visible
    and so unrelated shell parameter expansions inside command strings
    survive untouched.
    """
    if isinstance(value, str):
        out = value
        for key, repl in mapping.items():
            out = out.replace("${" + key + "}", repl)
        return out
    if isinstance(value, dict):
        return {k: _substitute(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, mapping) for v in value]
    return value


def _inject_network_allowlist(profile: Dict[str, Any]) -> None:
    """Merge the user's ``network.json`` allowlist into manifest env.

    Read ``<config>/network.json``; when ``enabled: true`` and the
    allowlist is non-empty, set ``manifest.environment.<env>`` to a
    comma-joined hosts string so the in-container init step can apply
    iptables egress rules. Profiles that set the env explicitly win
    (including to empty string — that's how a user opts out of
    auto-discovery).
    """
    manifest = profile.setdefault("manifest", {})
    if not isinstance(manifest, dict):
        return
    environment = manifest.setdefault("environment", {})
    if not isinstance(environment, dict):
        return
    if _NETWORK_ALLOWLIST_ENV in environment:
        return  # Explicit profile value wins, including "".
    allowlist = _load_network_allowlist(get_config_dir())
    if allowlist:
        environment[_NETWORK_ALLOWLIST_ENV] = allowlist


def _load_network_allowlist(config_dir: Path) -> Optional[str]:
    """Load ``<config>/network.json`` and return a comma-joined allowlist.

    Returns ``None`` when the file is missing, ``enabled`` is false, or
    the allowlist is empty. Parse errors return ``None`` rather than
    propagating — network isolation falls open in that case, matching
    the in-container script's behavior when the env var is unset.
    """
    path = config_dir / "network.json"
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("enabled"):
        return None
    allowlist = data.get("allowlist")
    if not isinstance(allowlist, list):
        return None
    hosts = [str(h).strip() for h in allowlist if isinstance(h, str) and h.strip()]
    if not hosts:
        return None
    return ",".join(hosts)


def _parse_init(raw: Any) -> List[str]:
    """Parse the optional ``init:`` block — a list of shell command strings."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("sandbox.init must be a list of shell command strings")
    out: List[str] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                f"sandbox.init[{idx}] must be a non-empty shell command string"
            )
        out.append(item)
    return out


def _parse_terminal(raw: Any, *, run_as: Any) -> TerminalSpec:
    """Parse the optional ``terminal:`` block.

    Missing → use :class:`TerminalSpec` defaults. The block is a mapping
    with ``command`` (string, defaults to ``"bash -i"``), ``user``
    (string|None — falls back to top-level ``run_as``), and ``cwd``
    (string|None — caller resolves against the manifest root).
    """
    fallback_user = run_as if isinstance(run_as, str) and run_as.strip() else None
    if raw is None:
        return TerminalSpec(user=fallback_user)
    if not isinstance(raw, dict):
        raise ValueError("sandbox.terminal must be a mapping")

    command_raw = raw.get("command", "bash -i")
    if not isinstance(command_raw, str) or not command_raw.strip():
        raise ValueError("sandbox.terminal.command must be a non-empty string")

    user_raw = raw.get("user")
    if user_raw is not None and not isinstance(user_raw, str):
        raise ValueError("sandbox.terminal.user must be a string")
    user = user_raw if user_raw else fallback_user

    cwd_raw = raw.get("cwd")
    if cwd_raw is not None and not isinstance(cwd_raw, str):
        raise ValueError("sandbox.terminal.cwd must be a string")
    cwd = cwd_raw if cwd_raw else None

    return TerminalSpec(command=command_raw.strip(), user=user, cwd=cwd)


def _collect_layers(
    *,
    project_id: Optional[str],
    explicit_path: Optional[Path],
) -> List[tuple[Path, Dict[str, Any]]]:
    out: List[tuple[Path, Dict[str, Any]]] = []
    out.append((_DEFAULT_PROFILE_PATH, _load_yaml(_DEFAULT_PROFILE_PATH)))

    if explicit_path is not None:
        # Explicit overrides skip the layered chain — used only for tests
        # and scripting per the docstring.
        out.append((explicit_path.resolve(), _load_yaml(explicit_path)))
        return out

    cfg = get_config_dir()
    user_default = cfg / "sandbox" / "default.yml"
    if user_default.is_file():
        out.append((user_default, _load_yaml(user_default)))

    if project_id:
        project_profile = cfg / "projects" / project_id / "sandbox.yml"
        if project_profile.is_file():
            out.append((project_profile, _load_yaml(project_profile)))

    return out


def _load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Profile {path} must be a YAML mapping at the top level")
    return data


# ---------------------------------------------------------------------------
# Merge + substitute
# ---------------------------------------------------------------------------


def _deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge dicts; lists replace; scalars replace."""
    if not over:
        return dict(base)
    result: Dict[str, Any] = dict(base)
    for k, v in over.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# ---------------------------------------------------------------------------
# Service parsing + validation
# ---------------------------------------------------------------------------


def _parse_services(block: Mapping[str, Any]) -> List[ServiceSpec]:
    services: List[ServiceSpec] = []
    for name, spec in block.items():
        if not isinstance(spec, dict):
            raise ValueError(
                f"sandbox.services.{name} must be a mapping, got {type(spec).__name__}"
            )
        command = spec.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f"sandbox.services.{name}.command is required (string)")
        port = spec.get("port")
        if port is not None and not isinstance(port, int):
            raise ValueError(f"sandbox.services.{name}.port must be an integer")
        env = spec.get("env") or {}
        if not isinstance(env, dict):
            raise ValueError(f"sandbox.services.{name}.env must be a mapping")
        user = spec.get("user")
        if user is not None and not isinstance(user, str):
            raise ValueError(f"sandbox.services.{name}.user must be a string")
        services.append(
            ServiceSpec(
                name=name,
                command=command,
                port=port,
                env={str(k): str(v) for k, v in env.items()},
                user=user,
            )
        )
    return services


def _validate(profile: Dict[str, Any], services: List[ServiceSpec]) -> None:
    # Every service.port must be declared in options.exposed_ports so the
    # SDK actually publishes it. Catching this here saves a confusing
    # resolve_exposed_port failure later.
    declared = set((profile.get("options") or {}).get("exposed_ports") or [])
    for svc in services:
        if svc.port is not None and svc.port not in declared:
            raise ValueError(
                f"sandbox.services.{svc.name}.port={svc.port} is not in "
                f"sandbox.options.exposed_ports={sorted(declared)}"
            )

    # client.type is the only universally-required field.
    client_block = profile.get("client")
    if not isinstance(client_block, dict) or not client_block.get("type"):
        raise ValueError("sandbox.client.type is required")
