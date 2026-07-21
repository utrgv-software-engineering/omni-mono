"""Sandbox profile loading, layering, and substitution.

Lifted from omni-code: products bind ``resolve_profile`` to their config
directory and packaged default profile.

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import yaml

from omniagents.core.utils.merge import deep_merge as _deep_merge

# Env var the in-container `apply-network-isolation.sh` reads. Set on the
# manifest environment so the entrypoint init step can apply iptables
# allowlist rules. Auto-populated from ``<config>/network.json`` unless
# the profile sets it explicitly.
NETWORK_ALLOWLIST_ENV = "OMNI_SANDBOX_NETWORK_ALLOWLIST"


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
    config_dir: Optional[Path] = None,
    default_profile_path: Optional[Path] = None,
    project_id: Optional[str] = None,
    explicit_path: Optional[Path] = None,
) -> ResolvedProfile:
    """Resolve a profile through the layered chain.

    *workspace_dir* is the host directory the agent edits in-place; it
    replaces every ``${workspace_dir}`` occurrence in the merged YAML.

    *config_dir* is the product config directory holding the user layers
    (``sandbox/default.yml``, ``projects/<id>/sandbox.yml``) plus the
    ``network.json`` allowlist and user ``.env`` that are injected into
    the manifest environment. When omitted it defaults to the installed
    product identity's config dir
    (``omniagents.core.product.product_info().config_dir()``); an
    explicit argument wins. *default_profile_path* is the product's
    packaged base profile (skipped when None or missing).

    When *explicit_path* is given, only the built-in default + the explicit
    file are merged — the user/project layers are skipped. This is the
    "tests, scripting, debugging" path; the launcher should not use it.
    """
    if config_dir is None:
        from omniagents.core.product import product_info

        config_dir = product_info().config_dir()
    layers = _collect_layers(
        config_dir=config_dir,
        default_profile_path=default_profile_path,
        project_id=project_id,
        explicit_path=explicit_path,
    )
    merged: Dict[str, Any] = {}
    for _src, data in layers:
        merged = _deep_merge(merged, data)

    merged = _substitute(merged, {"workspace_dir": str(workspace_dir.resolve())})
    _inject_network_allowlist(merged, config_dir)
    _inject_user_env(merged, config_dir)
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


def _manifest_env_values(profile: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the mutable ``{KEY: value}`` map the SDK reads as the container
    environment, creating the nesting if absent.

    The SDK's ``Manifest.environment`` is an ``Environment`` model whose only
    field is ``value`` — i.e. the wire shape is ``environment: {value: {KEY:
    val}}``, NOT a flat ``environment: {KEY: val}`` (a flat dict is silently
    dropped to ``Environment(value={})``, so the container gets no env). Both
    injectors below must write into the inner ``value`` map. Returns ``None``
    when ``manifest`` / ``environment`` / ``value`` isn't a mapping.
    """
    manifest = profile.setdefault("manifest", {})
    if not isinstance(manifest, dict):
        return None
    environment = manifest.setdefault("environment", {})
    if not isinstance(environment, dict):
        return None
    values = environment.setdefault("value", {})
    return values if isinstance(values, dict) else None


def _inject_network_allowlist(profile: Dict[str, Any], config_dir: Path) -> None:
    """Merge the user's ``network.json`` allowlist into manifest env.

    Read ``<config>/network.json``; when ``enabled: true`` and the
    allowlist is non-empty, set ``manifest.environment.value.<env>`` to a
    comma-joined hosts string so the in-container init step can apply
    iptables egress rules. Profiles that set the env explicitly win
    (including to empty string — that's how a user opts out of
    auto-discovery).
    """
    allowlist = _load_network_allowlist(config_dir)
    if not allowlist:
        return  # nothing to inject — don't materialize the env nesting
    values = _manifest_env_values(profile)
    if values is None or NETWORK_ALLOWLIST_ENV in values:
        return  # bad shape, or explicit profile value wins (including "")
    values[NETWORK_ALLOWLIST_ENV] = allowlist


def _inject_user_env(profile: Dict[str, Any], config_dir: Path) -> None:
    """Merge the launcher's user ``.env`` (Settings → Environment) into the
    manifest environment, so the variables reach the *sandbox container* — the
    agent's bash/exec tools and the Terminal — not just the ``omni serve``
    process.

    Reads ``<config>/.env`` (the same config dir the network allowlist
    keys on; the launcher writes the merged ``.env`` there in every mode). Each
    key is set on ``manifest.environment.value`` only when the profile (or an
    earlier injector) hasn't already set it — explicit profile values win, and we
    never override the network-allowlist env. Parse/IO errors fall open (no env
    added) rather than break sandbox bring-up.
    """
    env_file = _load_env_file(config_dir)
    if not env_file:
        return  # no user .env — don't materialize the env nesting
    values = _manifest_env_values(profile)
    if values is None:
        return
    for key, value in env_file.items():
        if key not in values:  # explicit profile / allowlist value wins
            values[key] = value


def _load_env_file(config_dir: Path) -> Dict[str, str]:
    """Parse ``<config>/.env`` into a ``{KEY: value}`` map. Uses ``python-dotenv``
    when available (handles quoting/escapes the way the Settings editor writes),
    falling back to a minimal ``KEY=value`` parser. Missing file / parse errors
    return ``{}``."""
    path = config_dir / ".env"
    if not path.is_file():
        return {}
    try:
        try:
            from dotenv import dotenv_values  # type: ignore

            return {k: v for k, v in (dotenv_values(str(path)) or {}).items() if k and v is not None}
        except ImportError:
            out: Dict[str, str] = {}
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                if key:
                    out[key] = val.strip().strip('"').strip("'")
            return out
    except OSError:
        return {}


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
    config_dir: Path,
    default_profile_path: Optional[Path],
    project_id: Optional[str],
    explicit_path: Optional[Path],
) -> List[tuple[Path, Dict[str, Any]]]:
    out: List[tuple[Path, Dict[str, Any]]] = []
    if default_profile_path is not None and default_profile_path.is_file():
        out.append((default_profile_path, _load_yaml(default_profile_path)))

    if explicit_path is not None:
        # Explicit overrides skip the layered chain — used only for tests
        # and scripting per the docstring.
        out.append((explicit_path.resolve(), _load_yaml(explicit_path)))
        return out

    cfg = config_dir
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


# _deep_merge lives in omniagents.core.utils.merge — one merge semantics
# for every layered config (sandbox profiles, project overlays).


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
