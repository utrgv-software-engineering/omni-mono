"""Framework-owned product CLI host, driven by project.yml.

A *product* (a project shipping an agent as an installable distribution)
used to hand-roll its own CLI dispatcher — a reserved-word table, a
``--`` prompt escape, per-subcommand thin wrapper modules, an update
notice, and a fallthrough to ``omniagents run``. This module makes the
project manifest the single source of that surface: a product's console
script is the framework's :func:`main` directly, with the manifest
located by entry-point discovery
(:func:`omniagents.core.product.discover_product_manifest`)::

    [project.scripts]
    omni = "omniagents.product_cli:main"
    omni-setup = "omniagents.product_cli:setup_main"

    [project.entry-points."omniagents.products"]
    omni-code = "omni_code"

so if the base command for a product is ``omni``, the update command is
``omni update`` — no per-product dispatcher files. Embedders with a
manifest in hand can call :func:`product_main` directly.

:func:`product_main` always runs :func:`~omniagents.core.product.bootstrap_product`
first, so every framework subsystem (model store dir, platform policy
slug + credential store, codex token store, updater knobs) defaults from
the manifest's ``product:`` section. It then mounts the reserved
subcommands from convention:

- ``model`` / ``provider`` — the framework model/provider CLI, operating
  on the installed process-wide model store (a generic store rooted at
  the product config dir is installed when the product hasn't installed
  its own).
- ``auth`` — platform login/logout/status plus the ``auth codex``
  passthrough.
- ``update`` — mounted ONLY when the manifest declares a
  ``product.update:`` block (:meth:`UpdaterConfig.from_product`).
- ``sessions`` — the framework sessions CLI pinned to the product slug
  as project and the manifest's entrypoint agent as the default agent
  (:func:`run_product_sessions_command`, public so overrides can
  delegate).
- ``serve`` / ``sandbox-host`` — the launcher serve contract
  (:mod:`omniagents.product_serve`): a long-lived sandbox-session agent
  server with the JSON readiness handshake, and the laptop half of
  computer-as-sandbox. See ``docs/serve-protocol.md``.
- ``describe`` — machine-readable product introspection
  (:func:`run_product_describe_command`): identity, version, config dir,
  update channel, and the serve protocol version, as one JSON object.

Everything else falls through to
``omniagents run --project <manifest> --mode <cli.default_mode>`` with
the remaining args passed through; a leading ``--`` forces prompt mode
even for reserved words. The fallthrough path also carries two
framework conventions:

- **Embedded mode** (launcher contract, see ``docs/serve-protocol.md``):
  a ``--embedded`` flag anywhere in a fallthrough invocation is
  stripped; browser auto-open (``OMNI_WEB_AUTO_OPEN=false``) and the
  startup update notice (``<ENV_PREFIX>_UPDATE_CHECK=0``) are
  suppressed, and ``--mode web`` without an explicit ``--output`` gets
  ``--output json`` appended so the readiness payload is machine
  readable.
- **Models bootstrap** — for products with an installed process-wide
  model store (built at bootstrap from the manifest's declared model
  catalog): load the user model config, apply platform model
  governance, export ``MODEL_NAME``, and when no models are configured
  run the interactive setup wizard — or, in embedded mode, print one
  ``{"error": ...}`` JSON line and exit 1.

The update notice (when ``update:`` is declared) prints on the
fallthrough path only.

Manifest schema (top-level, all optional)::

    cli:
      default_mode: ink                    # mode for the fallthrough run
      extensions: my_product.cli_extensions   # extension module (see below)

Extension-module contract — the seam that keeps product-specific CLI
behavior in product code without a product dispatcher file. The module
named by ``cli.extensions`` may define:

- ``PREFLIGHT: list[Callable[[list[str]], list[str] | None]]`` — run in
  declaration order before any dispatch, each receiving the current
  argv (after any mutation by earlier preflights). A preflight may veto
  the run (``sys.exit``/``os.exec``) or return a modified argv; ``None``
  keeps argv unchanged. Covers product-specific gates like governance
  sandbox re-exec (first-run model wizards and embedded-mode
  interception are framework defaults now — see above).
- ``COMMANDS: dict[str, Callable[[list[str]], int | None]]`` — extra
  subcommands (e.g. ``serve``) or overrides of reserved ones, each
  receiving the argv after the command word and returning an exit
  status (``None`` → 0). An override may delegate to the framework
  default (e.g. :func:`run_product_sessions_command`). The first line of
  a handler's docstring is used in the top-level help.

Dispatch precedence: PREFLIGHT → COMMANDS (product) → reserved
(framework) → fallthrough ``omniagents run``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

import yaml

from omniagents.core.product import (
    PRODUCT_ENTRY_POINT_GROUP,
    ProductInfo,
    bootstrap_product,
    product_info,
)

__all__ = [
    "HELP_TOKENS",
    "RESERVED_COMMANDS",
    "ProductCliConfig",
    "dispatches_to_command",
    "load_product_cli_config",
    "main",
    "product_main",
    "run_product_describe_command",
    "run_product_sessions_command",
    "setup_main",
]

#: Subcommands the framework mounts from convention (``update`` only when
#: the manifest declares a ``product.update:`` block).
RESERVED_COMMANDS = (
    "model",
    "provider",
    "auth",
    "update",
    "sessions",
    "serve",
    "sandbox-host",
    "describe",
)

#: Tokens rendering the top-level help instead of dispatching.
HELP_TOKENS = frozenset({"-h", "--help", "help"})

_KNOWN_CLI_KEYS = {"default_mode", "extensions"}

Preflight = Callable[[List[str]], Optional[List[str]]]
CommandHandler = Callable[[List[str]], Optional[int]]


@dataclass(frozen=True)
class ProductCliConfig:
    """Light-parsed CLI knobs from the manifest (``cli:`` + ``agents.entrypoint``)."""

    default_mode: str = "ink"
    """Mode passed to ``omniagents run`` on the fallthrough path."""

    extensions: Optional[str] = None
    """Import path of the product's CLI extension module, or None."""

    entrypoint_agent: Optional[str] = None
    """The manifest's entrypoint agent — declared ``agents.entrypoint``,
    else the conventional one (the agent named after the product's
    ``prog``, else its ``slug``). The default agent for the reserved
    ``sessions`` command."""


def _resolve_manifest_path(project_path: Any) -> Path:
    p = Path(project_path).expanduser()
    if p.is_dir():
        p = p / "project.yml"
    if not p.is_file():
        raise FileNotFoundError(f"Could not find project manifest: {p}")
    return p


def _opt_str(data: Mapping[str, Any], key: str, *, where: str) -> Optional[str]:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}.{key} must be a non-empty string, got {value!r}")
    return value.strip()


def _conventional_entrypoint(data: Mapping[str, Any], manifest: Path) -> Optional[str]:
    """The conventional entrypoint agent, mirroring the project loader:
    the agent named after the product's ``prog``, else its ``slug``,
    when that agent exists under the manifest's agents dir."""
    section = data.get("product")
    if section is None:
        return None
    try:
        from omniagents.core.product import product_info_from_mapping

        pinfo = product_info_from_mapping(section)
    except ValueError:
        return None
    paths_cfg = data.get("paths")
    agents_rel = (
        paths_cfg.get("agents") if isinstance(paths_cfg, Mapping) else None
    )
    if not isinstance(agents_rel, str) or not agents_rel.strip():
        agents_rel = "agents"
    agents_dir = manifest.parent / agents_rel.strip()
    for candidate in (pinfo.prog, pinfo.slug):
        if (agents_dir / candidate / "agent.yml").is_file():
            return candidate
    return None


def load_product_cli_config(project_path: Any) -> ProductCliConfig:
    """Light-parse the ``cli:`` section (and ``agents.entrypoint``) of a manifest.

    Like :func:`omniagents.core.product.load_product_info`, this is a
    plain YAML read of the base manifest — no project resolution.
    Raises ``ValueError`` on unknown ``cli:`` keys (typo protection).
    """
    p = _resolve_manifest_path(project_path)
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Project manifest is not a mapping: {p}")

    agents_section = data.get("agents") or {}
    entrypoint = None
    if isinstance(agents_section, Mapping):
        raw_entry = agents_section.get("entrypoint")
        if isinstance(raw_entry, str) and raw_entry.strip():
            entrypoint = raw_entry.strip()
    if entrypoint is None:
        entrypoint = _conventional_entrypoint(data, p)

    section = data.get("cli")
    if section is None:
        return ProductCliConfig(entrypoint_agent=entrypoint)
    if not isinstance(section, Mapping):
        raise ValueError(
            f"project.yml 'cli:' must be a mapping, got {type(section).__name__} (file: {p})"
        )
    unknown = sorted(set(section) - _KNOWN_CLI_KEYS)
    if unknown:
        raise ValueError(
            f"Unknown key(s) in project.yml 'cli:': {', '.join(unknown)}. "
            f"Known keys: {', '.join(sorted(_KNOWN_CLI_KEYS))} (file: {p})"
        )
    return ProductCliConfig(
        default_mode=_opt_str(section, "default_mode", where="cli") or "ink",
        extensions=_opt_str(section, "extensions", where="cli"),
        entrypoint_agent=entrypoint,
    )


def _load_extensions(
    module_path: Optional[str],
) -> tuple[List[Preflight], Dict[str, CommandHandler]]:
    """Import the extension module and validate its PREFLIGHT/COMMANDS."""
    if not module_path:
        return [], {}
    from importlib import import_module

    try:
        module = import_module(module_path)
    except ImportError as e:
        raise ImportError(
            f"Could not import CLI extensions module {module_path!r} "
            f"(declared in project.yml 'cli.extensions'): {e}"
        ) from e

    preflights_raw = getattr(module, "PREFLIGHT", [])
    if not isinstance(preflights_raw, Sequence) or isinstance(preflights_raw, (str, bytes)):
        raise ValueError(f"{module_path}.PREFLIGHT must be a list of callables")
    preflights: List[Preflight] = []
    for hook in preflights_raw:
        if not callable(hook):
            raise ValueError(f"{module_path}.PREFLIGHT entries must be callable, got {hook!r}")
        preflights.append(hook)

    commands_raw = getattr(module, "COMMANDS", {})
    if not isinstance(commands_raw, Mapping):
        raise ValueError(f"{module_path}.COMMANDS must be a mapping of name -> callable")
    commands: Dict[str, CommandHandler] = {}
    for name, handler in commands_raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{module_path}.COMMANDS keys must be non-empty strings")
        if not callable(handler):
            raise ValueError(f"{module_path}.COMMANDS[{name!r}] must be callable")
        commands[name.strip()] = handler

    return preflights, commands


def dispatches_to_command(
    argv: Sequence[str], *, extra_commands: Sequence[str] = ()
) -> bool:
    """Whether *argv* dispatches to a subcommand (vs the fallthrough agent run).

    The gate product preflights use to run only on agent-run
    invocations. A leading ``--`` forces prompt mode (never a command);
    help tokens count as command dispatch; ``update`` counts only when
    the installed product identity declares an ``update:`` block.
    *extra_commands* is the product's own command names (its ``COMMANDS``
    keys).
    """
    argv = list(argv or [])
    if not argv:
        return False
    head = argv[0]
    if head == "--":
        return False
    if head in HELP_TOKENS:
        return True
    if head in set(extra_commands):
        return True
    if head == "update":
        return product_info().update is not None
    return head in RESERVED_COMMANDS


# ---------------------------------------------------------------------------
# Reserved-command handlers
# ---------------------------------------------------------------------------


def _require_model_store():
    """The installed process-wide model store; a generic product-config
    store is installed when the product hasn't bound its own (typically
    at import time of its models module or CLI extensions)."""
    from omniagents.core.models import ModelConfigStore, model_store, set_model_store

    store = model_store()
    if store is not None:
        return store
    info = product_info()
    store = ModelConfigStore(config_dir=info.config_dir)
    set_model_store(
        store,
        setup_hint=f"No model configured. Run `{info.prog} model setup` to add one.",
    )
    return store


def _run_model_command(args: List[str]) -> int:
    from omniagents.core.models.cli import run_model_command

    run_model_command(args, store=_require_model_store())
    return 0


def _run_provider_command(args: List[str]) -> int:
    from omniagents.core.models.cli import run_provider_command

    run_provider_command(args, store=_require_model_store())
    return 0


def _run_auth_command(args: List[str]) -> int:
    args = list(args or [])
    if args and args[0] == "codex":
        from omniagents.cli import run_codex_auth_command

        return run_codex_auth_command(args[1] if len(args) > 1 else "login")

    if not args or args[0] in HELP_TOKENS:
        prog = product_info().prog
        print(
            f"usage: {prog} auth <command>\n\n"
            "Commands:\n"
            "  login [url]    Authenticate with the management plane\n"
            "  logout         Clear stored credentials\n"
            "  status         Show current authentication status\n"
            "  codex <cmd>    ChatGPT (Codex) sign-in: login|logout|status\n"
        )
        return 0

    from omniagents.cli import run_platform_auth_command

    return run_platform_auth_command(args)


def _run_update_command(args: List[str]) -> int:
    from omniagents.core.updater import run_update_command

    return run_update_command(args)


def _product_version(info: ProductInfo) -> str:
    """The installed product distribution's version, falling back to the
    framework's own version when the product package has no metadata
    (editable checkouts, the bare framework identity)."""
    from importlib import metadata

    try:
        return metadata.version(info.name)
    except metadata.PackageNotFoundError:
        from omniagents._version import __version__

        return __version__


def run_product_describe_command(argv: Optional[List[str]] = None) -> int:
    """The reserved ``<prog> describe`` command — machine-readable product
    introspection for launchers and tooling.

    ``<prog> describe --json`` prints one JSON object: the resolved
    product identity (``name``/``prog``/``label``/``slug``), the
    installed ``version`` (product package metadata, framework version
    as fallback), the resolved absolute ``config_dir``, ``env_prefix``,
    the ``update`` channel (``{index_url, command_hint}`` or null), and
    ``serve_protocol`` — the version of the launcher serve contract this
    installation speaks (see docs/serve-protocol.md).
    """
    import argparse
    import json

    info = product_info()
    parser = argparse.ArgumentParser(prog=f"{info.prog} describe")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the description as one JSON object (the only format).",
    )
    parser.parse_args(list(argv or []))

    from omniagents.product_serve import SERVE_PROTOCOL_VERSION

    payload: Dict[str, Any] = {
        "name": info.name,
        "prog": info.prog,
        "label": info.label,
        "slug": info.slug,
        "version": _product_version(info),
        "config_dir": str(info.config_dir().expanduser().resolve()),
        "env_prefix": info.env_prefix,
        "update": (
            {
                "index_url": info.update.index_url,
                "command_hint": info.update.command_hint,
            }
            if info.update is not None
            else None
        ),
        "serve_protocol": SERVE_PROTOCOL_VERSION,
    }
    print(json.dumps(payload))
    return 0


def run_product_sessions_command(
    argv: Optional[List[str]] = None,
    *,
    project: Optional[str] = None,
    agent: Optional[str] = None,
    summary_envelope: Optional[Dict[str, Any]] = None,
    prog: Optional[str] = None,
) -> int:
    """The reserved ``<prog> sessions`` command — public so product
    overrides can wire their summarizer/envelope and delegate here.

    The product surface over the framework sessions CLI
    (``omniagents.core.session.cli``): a leading ``--agent`` flag
    (defaulting to *agent* — the manifest's entrypoint agent when
    mounted by :func:`product_main`) ahead of the subcommand, the
    project pinned to *project* (default: the product slug), and an
    optional product summary envelope for ``summarize``. Returns a
    process exit status.
    """
    import argparse

    from omniagents.core.session.cli import (
        add_sessions_subparser,
        handle_sessions_command,
    )

    info = product_info()
    prog = prog or info.prog
    project = project or info.slug
    argv = list(sys.argv[1:] if argv is None else argv)

    top = argparse.ArgumentParser(prog=f"{prog} sessions", add_help=False)
    top.add_argument("--agent", default=agent)
    ns, rest = top.parse_known_args(argv)

    framework = argparse.ArgumentParser(prog=prog)
    add_sessions_subparser(framework.add_subparsers(dest="command"))
    args = framework.parse_args(["sessions", *rest])
    args.project = project
    if not getattr(args, "agent", None):
        args.agent = ns.agent
    if summary_envelope is not None:
        args.summary_envelope = summary_envelope
    try:
        rc = handle_sessions_command(args)
    except RuntimeError as err:
        # Summarizer-installed model errors ("No model configured...")
        # surface as a clean message + exit 1, not a traceback.
        print(str(err))
        return 1
    return int(rc or 0)


# ---------------------------------------------------------------------------
# Fallthrough conventions: embedded mode + models bootstrap
# ---------------------------------------------------------------------------


def _get_arg_value(args: Sequence[str], *flags: str) -> Optional[str]:
    for i, arg in enumerate(args):
        if arg in flags and i + 1 < len(args):
            return args[i + 1]
    return None


def _intercept_embedded(
    args: List[str], info: ProductInfo
) -> tuple[List[str], bool]:
    """Embedded (desktop-launcher) mode for the fallthrough agent run.

    Part of the launcher contract (``docs/serve-protocol.md``): strip
    ``--embedded``, suppress browser auto-open and the startup update
    notice (the launcher owns update UX), and force JSON output for web
    mode unless ``--output`` was given explicitly. Returns the rewritten
    argv and whether embedded mode is active.
    """
    if "--embedded" not in args:
        return args, False

    args = [a for a in args if a != "--embedded"]
    os.environ["OMNI_WEB_AUTO_OPEN"] = "false"
    if info.env_prefix:
        os.environ[f"{info.env_prefix}_UPDATE_CHECK"] = "0"
    if (
        _get_arg_value(args, "--mode", "-m") == "web"
        and _get_arg_value(args, "--output") is None
    ):
        args = [*args, "--output", "json"]
    return args, True


def _bootstrap_models(*, embedded: bool) -> None:
    """Default models bootstrap ahead of the fallthrough agent run.

    Runs only for products with an installed process-wide model store —
    installed at :func:`bootstrap_product` from the manifest's declared
    model catalog (``paths.model_catalog``), or bound explicitly by the
    product. Loads the user model config, applies platform model
    governance when authenticated, exports ``MODEL_NAME`` from the
    resolved runtime, and when no models are configured runs the
    interactive setup wizard — or, in embedded mode, prints a single
    ``{"error": ...}`` JSON line for the launcher and exits 1.
    """
    from omniagents.core.models import model_store

    store = model_store()
    if store is None:
        return

    store.load_config()

    # Apply platform model governance if authenticated.
    try:
        from omniagents.core.models.governed import apply_platform_model_governance

        apply_platform_model_governance()
    except Exception:
        pass

    if "MODEL_NAME" not in os.environ:
        model_runtime = store.resolve_model_for_runtime()
        if model_runtime.get("model"):
            os.environ["MODEL_NAME"] = model_runtime["model"]

    if not store.has_models_configured():
        if embedded:
            import json

            print(json.dumps({"error": "No model credentials configured"}))
            sys.exit(1)
        print("No model credentials configured. Starting setup wizard...\n")
        from omniagents.core.models.cli import run_model_command

        run_model_command(["setup"], store=store)
        print()


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------


def _handler_summary(handler: CommandHandler) -> str:
    doc = (getattr(handler, "__doc__", None) or "").strip()
    if doc:
        return doc.splitlines()[0].strip()
    return "Product-defined command"


def _print_top_level_help(
    info: ProductInfo, commands: Dict[str, CommandHandler]
) -> None:
    prog = info.prog
    reserved: List[tuple[str, str]] = [
        ("auth", "Authenticate with the management plane"),
        ("model", "Manage model configuration"),
        ("provider", "Manage provider configuration"),
    ]
    if info.update is not None:
        reserved.append(("update", "Manage updates"))
    reserved.append(("sessions", "Inspect OmniAgents sessions"))
    reserved.append(("serve", "Run the agent server over a sandbox session"))
    reserved.append(("sandbox-host", "Expose this machine as a remote sandbox"))
    reserved.append(("describe", "Print product metadata as JSON"))

    entries: List[tuple[str, str]] = [(prog, f"Launch the {info.label} chat")]
    seen = set()
    for name, handler in commands.items():
        entries.append((f"{prog} {name} ...", _handler_summary(handler)))
        seen.add(name)
    for name, description in reserved:
        if name not in seen:
            entries.append((f"{prog} {name} ...", description))

    names = sorted(seen | {name for name, _ in reserved})
    width = max(len(left) for left, _ in entries) + 4
    lines = [f"usage: {prog} [{'|'.join(names)}] [args]", "", "Commands:"]
    for left, right in entries:
        lines.append(f"  {left:<{width}} {right}")
    lines += [
        "",
        "Prompt mode:",
        f"  {prog} <words...>{' ' * max(1, width - len(prog) - 13)} Send words as the initial chat prompt",
        f"  {prog} -- <words...>{' ' * max(1, width - len(prog) - 16)} Force prompt mode even for reserved words",
        "",
        f"Anything not matched above falls through to the standard {info.label} chat runtime.",
    ]
    print("\n".join(lines))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def product_main(project_path: Any, argv: Optional[List[str]] = None) -> int:
    """Run a product CLI invocation, driven entirely by its manifest.

    *project_path* is the product's packaged ``project.yml`` (or its
    directory). *argv* defaults to ``sys.argv[1:]``. Returns a process
    exit status (the fallthrough run may also exit via ``SystemExit``).
    See the module docstring for the dispatch contract.
    """
    manifest = _resolve_manifest_path(project_path)

    # Product identity first: everything after this — the extension
    # module's own preflights (governance policy slug, credential
    # store), the model store (config dir), the update notice — reads it
    # through the framework defaults.
    info = bootstrap_product(manifest)
    cli_config = load_product_cli_config(manifest)
    preflights, commands = _load_extensions(cli_config.extensions)

    args = list(sys.argv[1:] if argv is None else argv)

    # PREFLIGHT: product hooks, in order, before any dispatch. Each may
    # veto (exit/exec) or rewrite argv.
    for preflight in preflights:
        result = preflight(list(args))
        if result is not None:
            args = list(result)

    force_prompt_mode = bool(args and args[0] == "--")
    if force_prompt_mode:
        args = args[1:]

    if not force_prompt_mode and args:
        head = args[0]
        if head in HELP_TOKENS:
            _print_top_level_help(info, commands)
            return 0

        # Product commands win over reserved ones (override seam).
        handler = commands.get(head)
        if handler is not None:
            return int(handler(args[1:]) or 0)

        if head == "model":
            return _run_model_command(args[1:])
        if head == "provider":
            return _run_provider_command(args[1:])
        if head == "auth":
            return _run_auth_command(args[1:])
        if head == "update" and info.update is not None:
            return _run_update_command(args[1:])
        if head == "sessions":
            return run_product_sessions_command(
                args[1:], agent=cli_config.entrypoint_agent
            )
        if head == "serve":
            from omniagents.product_serve import serve_main

            return serve_main(args[1:])
        if head == "sandbox-host":
            from omniagents.product_serve import sandbox_host_main

            return sandbox_host_main(args[1:])
        if head == "describe":
            return run_product_describe_command(args[1:])

    # Fallthrough: the product's agent run. Embedded interception and
    # the models bootstrap apply here only — never ahead of subcommands.
    args, embedded = _intercept_embedded(args, info)
    _bootstrap_models(embedded=embedded)

    # The update notice prints on the fallthrough path only (and the
    # embedded env knob above disables it through the standard
    # <PREFIX>_UPDATE_CHECK gate).
    if info.update is not None:
        from omniagents.core.updater import UpdaterConfig, maybe_print_update_notice

        maybe_print_update_notice(UpdaterConfig.from_product(info))

    from omniagents.cli import main as omni_main

    sys.argv = [
        "omniagents",
        "run",
        "--project",
        str(manifest),
        "--mode",
        cli_config.default_mode,
        *args,
    ]
    omni_main()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Console-script entry for a product distribution.

    Resolves the product's packaged manifest via entry-point discovery
    (:func:`omniagents.core.product.discover_product_manifest`) and runs
    :func:`product_main` — the target a product's ``[project.scripts]``
    names directly, with no product-side dispatcher module at all.
    *argv* defaults to ``sys.argv[1:]``.
    """
    from omniagents.core.product import discover_product_manifest

    manifest = discover_product_manifest()
    if manifest is None:
        print(
            "omniagents: no product distribution found — no installed "
            f"distribution declares an '{PRODUCT_ENTRY_POINT_GROUP}' "
            "entry point.",
            file=sys.stderr,
        )
        return 2
    return product_main(manifest, argv)


def setup_main() -> int:
    """The model setup wizard, standalone — for ``omni-setup``-style
    console scripts.

    Bootstraps the entry-point-discovered product first (identity +
    declared model catalog), then runs ``model setup`` on the installed
    store (a generic product-config-dir store when none is installed).
    """
    from omniagents.core.product import discover_product_manifest

    manifest = discover_product_manifest()
    if manifest is not None:
        bootstrap_product(manifest)

    from omniagents.core.models.cli import run_model_command

    run_model_command(["setup"], store=_require_model_store())
    return 0
