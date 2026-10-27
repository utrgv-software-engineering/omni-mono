"""Declarative eval-environment construction from ``evaluation.yml``.

The ``evaluation.environment`` block declares the environment as data —
a ``type`` naming a registered environment factory plus that factory's
knobs — instead of naming a project-side Python class. The framework
builds the environment from the declaration; products keep no
environment code at all.

The built-in ``local_workspace`` type composes the framework's
workspace-eval machinery end to end::

    evaluation:
      environment:
        type: local_workspace

        # LocalWorkspaceEnvironment knobs. Relative paths resolve
        # against the evaluations/ directory.
        fixtures_dir: fixtures/workspaces
        workspace_root: ~/.cache/my_product/eval-workspaces
        skills_subdir: .my_product/skills
        keep_tempdir_env: MY_EVAL_KEEP_TEMPDIR
        tempdir_prefix: my_eval_

        # Static env vars applied around every scenario. Values support
        # ${workspace} (the per-scenario temp dir) and ${workspace_root}
        # (the configured cache root); unknown ${...} tokens are left
        # as-is so misspellings stay visible.
        env:
          MY_WORKSPACE_ROOT: "${workspace}"
          MISE_TRUSTED_CONFIG_PATHS: "${workspace_root}"

        # Optional: compose ContainerRoutingMixin so scenarios can route
        # their shell/file surface into a container via a sandbox
        # profile. `profile_resolver: product` is the installed
        # product's layered resolver (resolve_product_profile); a dotted
        # path ("pkg.mod.attr") imports a custom resolver.
        container_routing:
          profile_resolver: product
          default_profile: profiles/devbox.yml   # for bare `environment.image`
          # profile: ...   # optional class-level fallbacks; per-scenario
          # image: ...     # `environment:` blocks win.

        # Optional: per-scenario user-MCP fixture staging (the
        # UserMcpFixtureStager path). When a scenario sets
        # `<scenario_key>: <name>`, an isolated XDG_CONFIG_HOME is
        # staged inside the temp dir with a generated
        # `<config_name>/mcp.json` pointing a real MCP server at
        # per-scenario state: `<dbs_dir>/<name>.db` is copied to
        # `<db_dest>` (or the server initializes it fresh) and
        # `<dbs_dir>/<name>.pages` to `<pages_dest>`.
        mcp_fixture:
          scenario_key: db_fixture
          dbs_dir: fixtures/dbs
          config_name: my_product
          server_name: my-mcp-server
          command: { env_var: MY_MCP_NODE, default: node }
          cli:
            env_var: MY_MCP_CLI
            default: ~/path/to/dist/cli.js
          args: ["${cli}", "--db-path", "${db_path}", "--pages-dir", "${pages_dir}"]
          db_dest: projects.db
          pages_dest: projects

Environments that genuinely cannot be expressed as configuration
register their own factory (typically from ``evaluations/measures.py``,
which the pipeline imports before the environment is built)::

    from omniagents.core.eval.environment_config import register_environment_type

    register_environment_type(
        "booking_api", lambda cfg, *, project_root, eval_dir: BookingEnv(**cfg)
    )

and declare ``environment: {type: booking_api, ...}``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from omniagents.core.eval.container_routing import ContainerRoutingMixin
from omniagents.core.eval.environment import EnvironmentContext, EvalEnvironment
from omniagents.core.eval.local_workspace import LocalWorkspaceEnvironment
from omniagents.core.eval.mcp_fixture import UserMcpFixtureStager

_logger = logging.getLogger(__name__)

__all__ = [
    "ConfiguredWorkspaceEnvironment",
    "McpFixtureConfig",
    "build_environment",
    "register_environment_type",
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _expand(value: str, mapping: Mapping[str, str]) -> str:
    """Replace ``${key}`` tokens in *value*; unknown tokens are left as-is."""
    out = value
    for key, repl in mapping.items():
        out = out.replace("${" + key + "}", repl)
    return out


def _resolve_path(value: Any, base: Optional[Path]) -> Path:
    """``~``-expand *value*; resolve relative paths against *base*."""
    p = Path(str(value)).expanduser()
    if not p.is_absolute() and base is not None:
        p = base / p
    return p


def _parse_env_overridable(value: Any, key: str) -> tuple[Optional[str], Optional[str]]:
    """Parse ``{env_var: ..., default: ...}`` (or a plain string default).

    Returns ``(env_var, default)``.
    """
    if value is None:
        return (None, None)
    if isinstance(value, str):
        return (None, value)
    if isinstance(value, Mapping):
        unknown = set(value) - {"env_var", "default"}
        if unknown:
            raise ValueError(
                f"mcp_fixture.{key}: unknown keys {sorted(unknown)} "
                "(expected env_var / default)"
            )
        env_var = value.get("env_var")
        default = value.get("default")
        return (
            str(env_var) if env_var else None,
            str(default) if default is not None else None,
        )
    raise ValueError(f"mcp_fixture.{key} must be a string or a mapping")


# ---------------------------------------------------------------------------
# MCP fixture staging (per-scenario user-MCP state)
# ---------------------------------------------------------------------------


@dataclass
class McpFixtureConfig:
    """Parameterizes per-scenario user-MCP fixture staging.

    The staging itself (isolated XDG_CONFIG_HOME, db/pages copy,
    generated mcp.json, env-var plumbing) is framework behavior; this
    holds the product's declared parts — which server, which fixture
    files, which cli binary.
    """

    dbs_dir: Path
    """Directory holding ``<name>.db`` / ``<name>.pages`` fixtures."""

    config_name: str
    """Product config-dir name — mcp.json lands at ``<xdg>/<config_name>/``."""

    server_name: str
    """Server key written into the generated ``mcpServers`` mapping."""

    scenario_key: str = "db_fixture"
    """Scenario field that names the fixture and opts staging in."""

    cli_env_var: Optional[str] = None
    """Env var that overrides the cli path (checked first, must be a file)."""

    cli_default: Optional[Path] = None
    """Declared default cli path used when the env var is unset."""

    command_env_var: Optional[str] = None
    """Env var that overrides the server command binary."""

    command_default: str = "node"
    """Server command binary used when the env var is unset."""

    db_dest: str = "db.sqlite3"
    """Destination filename for the copied ``<name>.db`` fixture."""

    pages_dest: str = "pages"
    """Destination directory name for the copied ``<name>.pages`` tree."""

    args: List[str] = field(
        default_factory=lambda: [
            "${cli}",
            "--db-path",
            "${db_path}",
            "--pages-dir",
            "${pages_dir}",
        ]
    )
    """Server args template; ``${cli}``/``${db_path}``/``${pages_dir}``
    expand to the resolved absolute paths."""

    def resolve_cli(self) -> Path:
        """Locate the MCP server cli.

        Resolution order: the env-var override (when set it must point
        at a file — no silent fallback), then the declared default path.
        """
        if self.cli_env_var:
            env_path = os.environ.get(self.cli_env_var)
            if env_path:
                p = Path(env_path).expanduser()
                if not p.is_file():
                    raise RuntimeError(
                        f"{self.cli_env_var}={env_path!r} does not point to a file"
                    )
                return p
        if self.cli_default is not None:
            p = Path(self.cli_default).expanduser()
            if p.is_file():
                return p
        hint = (
            f"Set {self.cli_env_var} to the cli path, or " if self.cli_env_var else ""
        )
        raise RuntimeError(
            f"Scenario specifies {self.scenario_key!r} but the MCP server cli "
            f"is not available. {hint}ensure the declared default path "
            f"exists: {self.cli_default}"
        )

    def resolve_command(self) -> str:
        if self.command_env_var:
            override = os.environ.get(self.command_env_var)
            if override:
                return override
        return self.command_default


# ---------------------------------------------------------------------------
# The built-in `local_workspace` environment
# ---------------------------------------------------------------------------


class _WorkspaceStagingEnvironment(LocalWorkspaceEnvironment):
    """LocalWorkspaceEnvironment plus the declared env map and MCP staging.

    Split from :class:`ConfiguredWorkspaceEnvironment` so the container
    mixin composes *around* it: MCP staging runs after workspace staging
    and before any container comes up, and the mixin's env-var remap
    sees the fully expanded map.
    """

    def __init__(
        self,
        fixtures_dir: str | Path | None = None,
        *,
        env: Optional[Mapping[str, Any]] = None,
        mcp_fixture: Optional[McpFixtureConfig] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(fixtures_dir=fixtures_dir, **kwargs)
        self.env_map: Dict[str, str] = {
            str(k): ("" if v is None else str(v)) for k, v in (env or {}).items()
        }
        self.mcp_fixture = mcp_fixture

    async def setup(self, scenario: Dict[str, Any]) -> EnvironmentContext:
        context = await super().setup(scenario)
        mf = self.mcp_fixture
        if mf is None:
            return context
        tmp_dir = context["tmp_dir"]

        # Stage MCP-backed state if the scenario requests it. Done last so
        # a failure here surfaces with the workspace already set up.
        fixture_key = scenario.get(mf.scenario_key)
        db_path: Optional[Path] = None
        mcp_config_dir: Optional[Path] = None
        mcp_env: Optional[Dict[str, str]] = None
        if fixture_key:
            mcp_cli = mf.resolve_cli()
            stager = UserMcpFixtureStager(tmp_dir, product_config_name=mf.config_name)
            db_path = stager.stage_file(mf.dbs_dir / f"{fixture_key}.db", mf.db_dest)
            pages_dir = stager.stage_tree(
                mf.dbs_dir / f"{fixture_key}.pages", mf.pages_dest
            )
            mapping = {
                "cli": str(mcp_cli),
                "db_path": str(db_path),
                "pages_dir": str(pages_dir),
            }
            stager.write_mcp_json(
                {
                    mf.server_name: {
                        "type": "stdio",
                        "command": mf.resolve_command(),
                        "args": [_expand(a, mapping) for a in mf.args],
                    }
                }
            )
            mcp_config_dir = stager.config_dir
            mcp_env = stager.env_vars()

            print(
                f"[{type(self).__name__}] {mf.scenario_key}={fixture_key!r}: "
                f"XDG_CONFIG_HOME={stager.xdg_root} db_path={db_path} "
                f"mcp_cli={mcp_cli}"
            )

        context.data.update(
            {
                mf.scenario_key: fixture_key,
                "db_path": str(db_path) if db_path else None,
                "mcp_config_dir": str(mcp_config_dir) if mcp_config_dir else None,
                "_mcp_env": mcp_env,
            }
        )
        return context

    def get_env_vars(self, context: EnvironmentContext) -> Dict[str, str]:
        env = super().get_env_vars(context)
        workspace = context.get("workspace_root", "") if context else ""
        mapping = {
            "workspace": workspace,
            "workspace_root": str(self.workspace_cache_dir),
        }
        for key, value in self.env_map.items():
            env[key] = _expand(value, mapping)
        # The staged XDG_CONFIG_HOME (mcp_fixture scenarios) — the runner
        # applies these before spec load, where user-MCP config is read.
        mcp_env = context.get("_mcp_env") if context else None
        if isinstance(mcp_env, dict):
            env.update(mcp_env)
        return env


class ConfiguredWorkspaceEnvironment(
    ContainerRoutingMixin, _WorkspaceStagingEnvironment
):
    """The ``type: local_workspace`` declarative environment.

    Host tempdir staging + declared env vars + optional MCP fixture
    staging, with container routing composed on top: scenarios that name
    a sandbox ``profile`` (or ``image``) get their shell/file surface
    routed into a container; scenarios without one fall through to plain
    host-tempdir behavior unchanged.
    """

    def __init__(
        self,
        *,
        profile_resolver: Optional[Callable[..., Any]] = None,
        default_profile_path: str | Path | None = None,
        profile: Optional[str] = None,
        image: Optional[str] = None,
        profile_base_dir: str | Path | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if profile_base_dir is not None:
            self.profile_base_dir = Path(profile_base_dir)
        if profile_resolver is not None:
            self.profile_resolver = profile_resolver
        if default_profile_path is not None:
            self.default_profile_path = Path(default_profile_path)
        # Optional class-level fallbacks; per-scenario `environment:` wins.
        self.default_profile = profile
        self.default_image = image

    def container_env_vars(self, context: EnvironmentContext) -> Dict[str, str]:
        # The declared env, with {workspace} as the container sees it; the
        # host's PATH and XDG config stay on the host.
        mapping = {
            "workspace": self.CONTAINER_WORKSPACE_ROOT,
            "workspace_root": self.CONTAINER_WORKSPACE_ROOT,
        }
        env = {"PWD": self.CONTAINER_WORKSPACE_ROOT}
        for key, value in self.env_map.items():
            env[key] = _expand(value, mapping)
        return env


# ---------------------------------------------------------------------------
# Factory registry + builder
# ---------------------------------------------------------------------------

EnvironmentFactory = Callable[..., EvalEnvironment]
"""``factory(config, *, project_root, eval_dir) -> EvalEnvironment``."""

_LOCAL_WORKSPACE_KEYS = {
    "fixtures_dir",
    "workspace_root",
    "skills_subdir",
    "keep_tempdir_env",
    "tempdir_prefix",
    "env",
    "container_routing",
    "mcp_fixture",
}

_CONTAINER_ROUTING_KEYS = {"profile_resolver", "default_profile", "profile", "image"}

_MCP_FIXTURE_KEYS = {
    "scenario_key",
    "dbs_dir",
    "config_name",
    "server_name",
    "command",
    "cli",
    "args",
    "db_dest",
    "pages_dest",
}


def _import_attr(ref: str) -> Any:
    """Import ``pkg.mod.attr`` (or ``pkg.mod:attr``)."""
    import importlib

    module_name, _, attr = ref.rpartition(":") if ":" in ref else ref.rpartition(".")
    if not module_name:
        raise ValueError(f"not an importable reference: {ref!r}")
    return getattr(importlib.import_module(module_name), attr)


def _parse_container_routing(
    block: Mapping[str, Any], base: Optional[Path]
) -> Dict[str, Any]:
    unknown = set(block) - _CONTAINER_ROUTING_KEYS
    if unknown:
        raise ValueError(
            f"environment.container_routing: unknown keys {sorted(unknown)}"
        )
    resolver_ref = block.get("profile_resolver", "product")
    if resolver_ref == "product":
        from omniagents.core.sandbox.profiles import resolve_product_profile

        resolver: Callable[..., Any] = resolve_product_profile
    else:
        resolver = _import_attr(str(resolver_ref))

    out: Dict[str, Any] = {"profile_resolver": resolver}
    if block.get("default_profile"):
        out["default_profile_path"] = _resolve_path(block["default_profile"], base)
    if block.get("profile"):
        out["profile"] = str(block["profile"])
    if block.get("image"):
        out["image"] = str(block["image"])
    return out


def _parse_mcp_fixture(
    block: Mapping[str, Any], base: Optional[Path]
) -> McpFixtureConfig:
    unknown = set(block) - _MCP_FIXTURE_KEYS
    if unknown:
        raise ValueError(f"environment.mcp_fixture: unknown keys {sorted(unknown)}")
    for required in ("dbs_dir", "config_name", "server_name"):
        if not block.get(required):
            raise ValueError(f"environment.mcp_fixture requires {required!r}")

    cli_env_var, cli_default = _parse_env_overridable(block.get("cli"), "cli")
    command_env_var, command_default = _parse_env_overridable(
        block.get("command"), "command"
    )

    kwargs: Dict[str, Any] = {
        "dbs_dir": _resolve_path(block["dbs_dir"], base),
        "config_name": str(block["config_name"]),
        "server_name": str(block["server_name"]),
        "cli_env_var": cli_env_var,
        # The cli default is a *declared* path (often outside the repo);
        # ~-expand it but leave relative refs alone until resolution.
        "cli_default": Path(cli_default).expanduser() if cli_default else None,
        "command_env_var": command_env_var,
    }
    if command_default is not None:
        kwargs["command_default"] = command_default
    if block.get("scenario_key"):
        kwargs["scenario_key"] = str(block["scenario_key"])
    if block.get("db_dest"):
        kwargs["db_dest"] = str(block["db_dest"])
    if block.get("pages_dest"):
        kwargs["pages_dest"] = str(block["pages_dest"])
    if block.get("args") is not None:
        args = block["args"]
        if not isinstance(args, list):
            raise ValueError("environment.mcp_fixture.args must be a list")
        kwargs["args"] = [str(a) for a in args]
    return McpFixtureConfig(**kwargs)


def _build_local_workspace(
    config: Mapping[str, Any],
    *,
    project_root: Optional[Path],
    eval_dir: Optional[Path],
) -> EvalEnvironment:
    base = eval_dir or project_root
    unknown = set(config) - _LOCAL_WORKSPACE_KEYS
    if unknown:
        raise ValueError(
            f"environment (type: local_workspace): unknown keys {sorted(unknown)}"
        )

    kwargs: Dict[str, Any] = {}
    if config.get("fixtures_dir"):
        kwargs["fixtures_dir"] = _resolve_path(config["fixtures_dir"], base)
    if config.get("workspace_root"):
        kwargs["workspace_cache_dir"] = Path(str(config["workspace_root"])).expanduser()
    for key in ("skills_subdir", "keep_tempdir_env", "tempdir_prefix"):
        if config.get(key):
            kwargs[key] = str(config[key])

    env_block = config.get("env")
    if env_block is not None:
        if not isinstance(env_block, Mapping):
            raise ValueError("environment.env must be a mapping of env vars")
        kwargs["env"] = env_block

    mcp_block = config.get("mcp_fixture")
    if mcp_block is not None:
        if not isinstance(mcp_block, Mapping):
            raise ValueError("environment.mcp_fixture must be a mapping")
        kwargs["mcp_fixture"] = _parse_mcp_fixture(mcp_block, base)

    routing_block = config.get("container_routing")
    if routing_block is not None:
        if not isinstance(routing_block, Mapping):
            raise ValueError("environment.container_routing must be a mapping")
        kwargs.update(_parse_container_routing(routing_block, base))
        kwargs["profile_base_dir"] = base

    return ConfiguredWorkspaceEnvironment(**kwargs)


_FACTORIES: Dict[str, EnvironmentFactory] = {
    "local_workspace": _build_local_workspace,
}


def register_environment_type(name: str, factory: EnvironmentFactory) -> None:
    """Register an environment factory under *name*.

    For environments whose behavior genuinely isn't expressible as
    configuration. Call it from project Python the pipeline already
    loads (``evaluations/measures.py``) — it runs before the environment
    is built.
    """
    _FACTORIES[str(name)] = factory


def _installed_factory(name: str) -> Optional[EnvironmentFactory]:
    """A factory an installed package provides as an ``omniagents.eval_environments`` entry point."""
    from importlib.metadata import entry_points

    for entry in entry_points(group="omniagents.eval_environments"):
        if entry.name == name:
            factory = entry.load()
            _FACTORIES[name] = factory
            return factory
    return None


def build_environment(
    env_config: Mapping[str, Any],
    *,
    project_root: Optional[Path] = None,
    eval_dir: Optional[Path] = None,
) -> EvalEnvironment:
    """Build the declared ``evaluation.environment``.

    *env_config* is the mapping from ``evaluation.yml``: a ``type``
    naming a registered factory plus that factory's knobs. Relative
    paths resolve against *eval_dir* (falling back to *project_root*).

    Raises ``ValueError`` for a missing/unknown ``type`` or malformed
    knobs — the pipeline reports the error and runs scenarios without an
    environment.
    """
    if not isinstance(env_config, Mapping):
        raise ValueError("evaluation.environment must be a mapping")
    type_name = env_config.get("type")
    if not type_name:
        raise ValueError(
            "evaluation.environment requires a 'type' naming a registered "
            f"environment factory (registered: {sorted(_FACTORIES)})"
        )
    factory = _FACTORIES.get(str(type_name)) or _installed_factory(str(type_name))
    if factory is None:
        raise ValueError(
            f"unknown evaluation environment type {str(type_name)!r} "
            f"(registered: {sorted(_FACTORIES)})"
        )
    config = {k: v for k, v in env_config.items() if k != "type"}
    return factory(config, project_root=project_root, eval_dir=eval_dir)
