"""Reserved ``<prog> serve`` / ``<prog> sandbox-host`` — the launcher contract.

Any product with a ``product:`` block in its project.yml gets a
launcher-compatible agent server from convention: ``<prog> serve`` brings
up a long-lived sandbox session (or the no-sandbox ``host`` target)
against the product's layered profile chain, runs the product's
entrypoint agent over it behind FastAPI/uvicorn, and emits the single
JSON readiness line the desktop launcher parses. ``<prog> sandbox-host``
is the laptop half of "computer-as-sandbox": it exposes this machine's
exec/file/PTY surface over a WebSocket for a cloud serve process.

Both are mounted as reserved subcommands by
:mod:`omniagents.product_cli`; a product may still override them via its
``cli.extensions`` ``COMMANDS`` mapping, but the defaults here are
launcher-complete. The wire contract (spawn argv, readiness payload,
error protocol) is versioned as :data:`SERVE_PROTOCOL_VERSION` and
documented in ``docs/serve-protocol.md`` — that document is what the
launcher targets; treat any change to the payload or flags as a protocol
revision.

Lifted from omni-code's ``serve_cli.py`` / ``sandbox_host_cli.py``; the
session lifecycle itself lives in
:mod:`omniagents.core.runtime.serve_harness`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import socket
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Version of the serve wire contract (spawn argv + readiness payload +
#: error protocol + sandbox-host handshake) documented in
#: ``docs/serve-protocol.md``. Reported by ``<prog> describe`` so the
#: launcher can verify compatibility before spawning.
SERVE_PROTOCOL_VERSION = 1

__all__ = [
    "SERVE_PROTOCOL_VERSION",
    "build_serve_parser",
    "build_readiness_payload",
    "serve_main",
    "sandbox_host_main",
    "exec_port_payload",
]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_serve_parser(prog: Optional[str] = None) -> argparse.ArgumentParser:
    """The ``<prog> serve`` argument parser — the launcher's spawn contract.

    Every flag here is part of the versioned serve protocol; the launcher
    builds its argv from exactly these (see docs/serve-protocol.md).
    """
    if prog is None:
        from omniagents.core.product import product_info

        prog = product_info().prog
    p = argparse.ArgumentParser(
        prog=f"{prog} serve",
        description=(
            "Run the product agent server on the host with a long-lived "
            "SandboxSession. Sandbox shape is read from the resolved profile "
            "chain (packaged default ← user-default ← per-project ← --profile). "
            "Workspace seeding is determined by --source flags."
        ),
    )
    p.add_argument(
        "--source",
        dest="sources",
        action="append",
        default=[],
        help=(
            "Per-source JSON descriptor; repeatable. Each source seeds one "
            "subdirectory of /workspace/<mountName>. Required JSON keys: "
            "``kind`` (local|local-git|git-remote), ``mountName`` (slug used "
            "as the subdir name). Kind-specific keys: "
            "local/local-git → ``path`` (host dir); "
            "git-remote → ``repoUrl``, optional ``ref``, optional ``auth`` "
            "({tokenEnv, username}) for private repos."
        ),
    )
    p.add_argument(
        "--credential",
        dest="credentials",
        action="append",
        default=[],
        help=(
            "Per-credential JSON descriptor; repeatable. Configures git + the "
            "host's CLI (gh / az devops) inside the sandbox at boot, for every "
            "linked host the project uses (not just cloned remotes). Keys: "
            "``url`` (a remote URL on the host, used to derive host + org), "
            "``username`` (HTTPS basic-auth user), ``tokenEnv`` (name of the "
            "env var holding the token — value read host-side, never on argv)."
        ),
    )
    p.add_argument(
        "--workspace",
        required=True,
        type=Path,
        help=(
            "Host directory the agent edits in-place. Substituted for "
            "``${workspace_dir}`` in the resolved profile (manifest root, "
            "env values, service commands, etc.). For unix_local this is "
            "the literal sandbox root; for clients that mount under "
            "/workspace it's used only where the profile references it."
        ),
    )
    p.add_argument(
        "--snapshot-dir",
        default=None,
        type=Path,
        help=(
            "Directory under which per-session snapshot tarballs are stored. "
            "When set, workspace state persists across session restarts "
            "(snapshot is restored on next open instead of re-running the "
            "seed entry). Without this flag, sessions are ephemeral."
        ),
    )
    p.add_argument(
        "--project",
        default=None,
        help=(
            "Launcher project ID; selects per-project profile layer. "
            "Snapshot keying is driven by --session-id, not this."
        ),
    )
    p.add_argument(
        "--session-id",
        default=None,
        help=(
            "Stable id for this resumable workspace. If omitted, a fresh "
            "uuid is generated and printed in the JSON readiness payload — "
            "the launcher captures it and passes it back on the next start "
            "to resume. Used as the snapshot tarball name "
            "(``{session-id}.tar``)."
        ),
    )
    p.add_argument(
        "--container-id",
        default=None,
        help=(
            "Docker container id captured from a previous launch's readiness "
            "payload. When supplied AND the resolved profile uses the docker "
            "backend, the SDK ``client.resume(state)`` path is taken instead "
            "of ``client.create(...)``: warm reattach to the original "
            "container if it still exists (preserves running processes / env "
            "/ apt+pip installs); silently falls back to fresh container + "
            "snapshot rehydrate if the container is gone. Ignored for non-"
            "docker backends. The readiness payload's ``resume`` field tells "
            "the launcher which tier actually happened."
        ),
    )
    p.add_argument(
        "--profile",
        default=None,
        type=Path,
        help=(
            "Explicit profile YAML; bypasses user/project layers (tests, "
            "scripting, and launcher-written per-session profiles)."
        ),
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument(
        "--port",
        type=int,
        default=0,
        help="0 = pick a free port (default).",
    )
    p.add_argument(
        "--auth-token",
        default=None,
        help="Bearer token required from WS clients.",
    )
    p.add_argument(
        "--agent-project",
        default=None,
        type=Path,
        help="Path to project.yml (default: the installed product's manifest).",
    )
    p.add_argument(
        "--agent",
        default=None,
        help="Agent key in project.yml (default: entrypoint).",
    )
    p.add_argument(
        "--output",
        choices=["json", "human"],
        default="json",
        help=(
            "Readiness line format. ``json`` (default) emits a single line "
            "matching the launcher's SandboxJsonPayload contract; ``human`` "
            "prints a short URL for terminal users."
        ),
    )
    return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pick_port(host: str, requested: int) -> int:
    if requested:
        return requested
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _resolve_agent_project_path(explicit: Optional[Path]) -> Path:
    """The project manifest serving the agent: --agent-project, else the
    installed product's packaged manifest."""
    if explicit is not None:
        return explicit.resolve()
    from omniagents.core.product import product_info

    manifest = product_info().manifest_path
    if manifest is not None and manifest.is_file():
        return manifest
    raise FileNotFoundError(
        "Could not locate project.yml (no product manifest installed). "
        "Pass --agent-project explicitly."
    )


def _load_spec(project_path: Path, agent_key: Optional[str]) -> Any:
    from omniagents.core.project.runtime import (
        build_spec_from_project,
        load_project,
    )
    from omniagents.core.runtime.serve_state import set_project_context

    proj = load_project(str(project_path))
    # Publish the resolved ProjectContext so worker spec loading (the
    # framework's serve-state-derived worker defaults) can route the worker
    # through build_spec_from_project with the same capability/tool/
    # guardrail registries. Without this, the worker would load its
    # agent.yml in isolation with capability_registry=None and silently
    # drop every entry in its `capabilities:` list, leaving it with zero
    # tools attached.
    set_project_context(proj)
    return build_spec_from_project(proj, agent_key=agent_key)


def _register_codex_provider() -> None:
    """Register the openai-oauth model provider builder when the product's
    declared model catalog contains an ``openai-oauth`` provider.

    Conventional: the same catalog gate
    :func:`omniagents.core.models.catalog.build_catalog_store` uses for
    the store's OAuth hooks. No catalog (or no oauth provider) → no-op.
    Never blocks serve.
    """
    try:
        from omniagents.core.models.catalog import (
            load_model_catalog,
            model_catalog_path,
        )
        from omniagents.core.product import product_info

        manifest = product_info().manifest_path
        if manifest is None:
            return
        catalog_path = model_catalog_path(manifest)
        if catalog_path is None:
            return
        if not load_model_catalog(catalog_path).has_oauth_provider():
            return
        from omniagents.core.providers.codex import register_codex_provider

        register_codex_provider()
    except Exception as e:
        print(
            json.dumps({"warning": f"codex provider registration skipped: {e}"}),
            file=sys.stderr,
        )


def build_readiness_payload(
    *,
    host: str,
    port: int,
    auth_token: Optional[str],
    session: Any,
    workspace: Any,
    profile: Any,
    service_urls: Dict[str, Any],
    resume_tier: Optional[str],
) -> Dict[str, Any]:
    """The launcher's ``SandboxJsonPayload`` readiness dict — the serve
    protocol's handshake, field-for-field (see docs/serve-protocol.md).

    *session* is the live sandbox session or ``None`` for the host
    target; *profile* is the :class:`ResolvedProfile` the session was
    built from.
    """
    base_url = f"http://{host}:{port}"
    ws_url = f"ws://{host}:{port}/ws"
    if auth_token:
        ws_url = f"{ws_url}?token={auth_token}"
    # Pull container_id from session.state when present (docker backends
    # expose it; unix_local / e2b / etc. don't). The launcher uses this
    # for ``docker exec``-based operations like PR diff extraction.
    container_id = (
        None if session is None else getattr(session.state, "container_id", None)
    )
    manifest_root = (
        workspace if session is None else getattr(session.state.manifest, "root", None)
    )
    return {
        "sandbox_url": base_url,
        "ws_url": ws_url,
        "ui_url": base_url,
        "services": service_urls,
        "ports": {"ui": port},
        "container_id": container_id,
        "resume": resume_tier,
        "_debug": {
            "client": (profile.raw.get("client") or {}).get("type"),
            "manifest_root": manifest_root,
            "profile_sources": [str(s) for s in profile.sources],
        },
    }


def _parse_json_flag_list(raw_values: List[str], flag: str) -> List[Dict[str, Any]]:
    """Parse a repeated JSON-descriptor flag (``--source`` / ``--credential``)."""
    parsed_list: List[Dict[str, Any]] = []
    for raw in raw_values:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"{flag} value is not valid JSON: {raw!r}") from e
        if not isinstance(parsed, dict):
            raise ValueError(f"{flag} must be a JSON object, got {parsed!r}")
        parsed_list.append(parsed)
    return parsed_list


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


async def _serve(args: argparse.Namespace) -> int:
    from omniagents.core.runtime.serve_harness import (
        _attach_session_to_spec,
        _cancel_active_runs,
        _install_host_terminal_manager,
        _install_session_terminal_manager,
        _is_host_profile,
        _profile_display_name,
        bring_up_host_target,
        bring_up_session,
        perform_switch,
        teardown_session,
    )
    from omniagents.core.sandbox.profiles import resolve_product_profile

    # --- Profile resolution ---
    # The conventional product chain: packaged default (paths.sandbox_profile
    # or the manifest-sibling sandbox_profile_default.yml) ← user default ←
    # per-project ← --profile.
    try:
        profile = resolve_product_profile(
            workspace_dir=args.workspace,
            project_id=args.project,
            explicit_path=args.profile,
        )
    except (FileNotFoundError, ValueError) as e:
        print(json.dumps({"error": f"profile: {e}"}), file=sys.stderr)
        return 1

    # --- Codex (ChatGPT OAuth) provider ---
    # Register the openai-oauth provider builder so the main agent can use a
    # Codex model whenever one is selected (default or switched mid-session) —
    # not only when it's the startup default. Aux agents (compact, recap,
    # summarizer) build their own via build_model_provider's openai-oauth
    # branch. Safe + idempotent regardless of sign-in state.
    _register_codex_provider()

    # --- Sandbox session ---
    # Parse the repeated --source JSON descriptors. Empty list = no
    # seeding (container boots with an empty /workspace).
    try:
        sources_parsed = _parse_json_flag_list(args.sources, "--source")
    except ValueError as e:
        print(json.dumps({"error": f"source: {e}"}), file=sys.stderr)
        return 1

    # Parse the repeated --credential JSON descriptors (boot-time git/CLI auth).
    try:
        credentials_parsed = _parse_json_flag_list(args.credentials, "--credential")
    except ValueError as e:
        print(json.dumps({"error": f"credential: {e}"}), file=sys.stderr)
        return 1

    # --- Session id ---
    # The stable identifier for this resumable workspace. Caller-supplied via
    # --session-id (resume) or auto-generated (first start). Emitted in the
    # readiness payload so the launcher can capture + persist it for next time.
    session_id = args.session_id or str(uuid.uuid4())

    # --- Bring the execution target up ---
    # ``host`` target: no sandbox session at all. Run the agent as a plain
    # Agent against the host filesystem; the harness binds a HostWorkspace as
    # the process-wide default so every run (including workers, which copy the
    # context) routes through it, and publishes manifest-root + sources.
    #
    # Sandbox targets: ``resume_tier`` is "reused"/"rehydrated"/"fresh"/None;
    # ``bring_up_session`` also publishes manifest-root, sources, and the
    # live-session slots, and tears the partial session down if init fails.
    is_host = _is_host_profile(profile.raw)
    if is_host:
        session = None
        resume_tier = None
        started_services = []
        service_urls: Dict[str, Any] = {}
        client_type = "host"
        bring_up_host_target(
            workspace=args.workspace,
            profile_raw=profile.raw,
            sources_parsed=sources_parsed,
        )
    else:
        from omniagents.core.sandbox.permissive_local_dir import (
            PermissiveLocalDirError,
        )

        try:
            brought = await bring_up_session(
                profile=profile,
                sources_parsed=sources_parsed,
                session_id=session_id,
                snapshot_dir=args.snapshot_dir,
                prior_container_id=args.container_id,
                credentials_parsed=credentials_parsed,
            )
        except (ValueError, PermissiveLocalDirError) as e:
            # Bad source descriptor (ValueError) or a workspace that failed to
            # seed — e.g. exceeding the seed-size cap. Emit a structured error
            # the launcher surfaces cleanly instead of crashing with a traceback.
            print(json.dumps({"error": f"source: {e}"}), file=sys.stderr)
            return 1
        session = brought.session
        resume_tier = brought.resume_tier
        started_services = brought.started_services
        service_urls = brought.service_urls
        client_type = brought.client_type

    # --- Agent spec + session injection ---
    agent_project_path = _resolve_agent_project_path(args.agent_project)
    spec = _load_spec(agent_project_path, args.agent)
    if not is_host:
        # Flip the spec to a SandboxAgent bound to the live session. The host
        # target leaves the spec as a plain Agent (no session, no binding).
        _attach_session_to_spec(spec, session, profile.raw)

    # Publish the now-sandbox-attached orchestrator spec so the worker spec
    # loader can forward sandbox_use_sandbox_agent / sandbox_run_as /
    # sandbox_run_config to spawned workers (loaded fresh per spawn, otherwise
    # their filesystem/shell tools would target the host, not the sandbox).
    from omniagents.core.runtime.serve_state import set_orchestrator_spec

    set_orchestrator_spec(spec)

    # --- FastAPI app + uvicorn ---
    import uvicorn

    from omniagents.backends.server.app import build_app
    from omniagents.core.runtime.serve_state import (
        get_manifest_root,
        get_sandbox_session,
    )
    from omniagents.workspace import clamp_within

    def _resolve_workspace_root(requested: Optional[str]) -> Optional[str]:
        """Map a requested cwd to the effective one, per the live target.

        Host target (no live sandbox session): the cwd may be any host dir —
        return it, or default to the workspace root. Sandbox target: clamp the
        cwd to within the manifest root, so it can navigate among container
        subdirectories but a host path can never root a sandbox run. Reads the
        live state each call, so it stays correct across runtime switches.
        """
        root = get_manifest_root()
        if get_sandbox_session() is None:
            if isinstance(requested, str) and requested.strip():
                return requested
            return root or args.workspace
        return clamp_within(requested, root) if root else requested

    app = build_app(
        config_path=str(agent_project_path),
        spec=spec,
        auth_token=args.auth_token,
        workspace_root_resolver=_resolve_workspace_root,
    )

    # Install the TerminalManager matching the execution target. Host →
    # HostPtyBackend (a host shell, by design). Sandbox → SessionPtyBackend
    # so the Terminal runs inside the sandbox (same user/root/network as the
    # agent's bash tool); a sandbox backend that can't stream a PTY gets a
    # backend that errors loudly — never a silent host shell.
    if is_host:
        _install_host_terminal_manager(
            app, profile.terminal, default_cwd=args.workspace
        )
    else:
        _install_session_terminal_manager(app, session, profile.terminal)

    # Idle auto-recap: fire a session recap once a conversation has gone
    # quiet (>=3min since the last completed turn, >=3 turns deep, never
    # twice without fresh activity in between). Stashed on app.state for
    # teardown + future inspection.
    from omniagents.core.autonomy import IdleTrigger, IdleTriggerScheduler
    from omniagents.core.runtime.recap import generate_and_broadcast_recap

    recap_scheduler = IdleTriggerScheduler(app.state.agent_service)
    recap_scheduler.register(
        IdleTrigger(
            name="recap",
            action=generate_and_broadcast_recap,
            idle_seconds=180.0,
            min_turns=3,
        )
    )
    app.state.recap_scheduler = recap_scheduler
    await recap_scheduler.start()

    port = _pick_port(args.host, args.port)
    config = uvicorn.Config(
        app,
        host=args.host,
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)

    # --- Readiness payload ---
    payload = build_readiness_payload(
        host=args.host,
        port=port,
        auth_token=args.auth_token,
        session=session,
        workspace=args.workspace,
        profile=profile,
        service_urls=service_urls,
        resume_tier=resume_tier,
    )
    container_id = payload["container_id"]
    if args.output == "json":
        # ``default=str`` coerces any stray non-JSON value (e.g. a PosixPath —
        # manifest.root can arrive as one via pydantic ``model_copy(update=)``,
        # which skips the ``str`` field coercion) to its string form. The
        # readiness payload is the launcher's handshake; it must never fail to
        # serialize and crash serve. str(PosixPath("/workspace")) == "/workspace",
        # so the launcher still receives the value it expects.
        print(json.dumps(payload, default=str), flush=True)
    else:
        backend = (profile.raw.get("client") or {}).get("type", "?")
        label = None
        try:
            from omniagents.core.product import product_info

            label = product_info().label
        except Exception:
            pass
        print(
            f"{label or 'Agent server'}: {payload['ui_url']}  "
            f"(profile backend={backend})",
            flush=True,
        )

    # --- Publish the switch context ---
    # Lets the ``sandbox.switch`` server function rebuild + re-attach the
    # sandbox in place (no process restart). ``ready_event`` starts set: the
    # sandbox is up. Cleared in the teardown ``finally`` below.
    # ``profile_resolver`` is the conventional product resolver (adds the
    # packaged default profile layer) so switches resolve profiles the same
    # way boot did.
    from omniagents.core.runtime.serve_state import SwitchContext, set_switch_context

    switch_ctx = SwitchContext(
        app=app,
        spec=spec,
        session_id=session_id,
        snapshot_dir=args.snapshot_dir,
        sources_parsed=sources_parsed,
        credentials_parsed=credentials_parsed,
        workspace_dir=args.workspace,
        project_id=args.project,
        host=args.host,
        port=port,
        auth_token=args.auth_token,
        profile_resolver=resolve_product_profile,
        current_profile_path=str(args.profile) if args.profile else None,
        current_profile_name=_profile_display_name(args.profile, profile),
        backend=client_type,
        container_id=container_id,
        service_urls=service_urls,
        started_services=started_services,
    )
    switch_ctx.ready_event.set()
    set_switch_context(switch_ctx)

    # The builtin ``sandbox.switch`` server function (activated by the
    # ``sandbox`` capability pack) validates the RPC and delegates to this
    # executor; the orchestration itself lives in the harness's
    # ``perform_switch``.
    from omniagents.core.runtime.sandbox_lifecycle import set_switch_executor

    set_switch_executor(perform_switch)

    # --- Run server + fast signal shutdown ---
    # Any exit signal skips uvicorn's graceful drain (``force_exit``): the
    # drain waits on open WS connections — all ours (launcher, terminals) —
    # and on the in-flight turn, which reads as a hang. Users follow a "hung"
    # Ctrl+C with a terminal close (SIGHUP) or SIGKILL, which skips the
    # teardown below and loses the workspace snapshot. The thing that
    # actually preserves work is that teardown — get to it immediately.
    def _request_stop(*_: object) -> None:
        server.should_exit = True
        server.force_exit = True

    loop = asyncio.get_running_loop()
    _handled_signals = [signal.SIGTERM, signal.SIGINT]
    # A closed terminal window delivers SIGHUP; unhandled it kills the
    # process instantly and the snapshot is never persisted. Absent on
    # Windows.
    if hasattr(signal, "SIGHUP"):
        _handled_signals.append(signal.SIGHUP)
    for sig in _handled_signals:
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_a: _request_stop())

    # uvicorn's ``serve()`` re-captures SIGINT/SIGTERM with its own handler
    # (``capture_signals`` → ``handle_exit``) whose first signal starts the
    # graceful drain we just opted out of. Override the hook on the instance
    # so every signal routes through ``_request_stop`` during serve too.
    def _handle_exit(sig: int, frame: object) -> None:  # noqa: ARG001
        _request_stop()

    server.handle_exit = _handle_exit  # type: ignore[method-assign]

    try:
        await server.serve()
    finally:
        from omniagents.core.runtime.serve_state import (
            get_discard_snapshot_on_exit,
            get_sandbox_session,
            get_switch_context,
            set_sandbox_session,
            set_switch_context,
        )

        # Quiesce before teardown: with the drain skipped (force_exit), an
        # in-flight run would otherwise keep mutating the workspace while
        # teardown tars it. Bounded — a hung run must not block the snapshot.
        try:
            await asyncio.wait_for(
                _cancel_active_runs(getattr(app.state, "agent_service", None)),
                timeout=5,
            )
        except Exception:
            pass

        # A switch may have replaced the session/services since startup — tear
        # down the LIVE ones, not the first-launch locals.
        live_ctx = get_switch_context()
        live_session = get_sandbox_session() or session
        live_services = (
            live_ctx.started_services if live_ctx is not None else started_services
        )

        # Stop the auto-recap watcher (cancels its task + detaches listeners).
        recap_scheduler_to_stop = getattr(app.state, "recap_scheduler", None)
        if recap_scheduler_to_stop is not None:
            try:
                await recap_scheduler_to_stop.stop()
            except Exception:
                pass
        # Stop the idle watcher first so its tick task is cancelled and
        # any in-flight pause unwinds before teardown does its own
        # defensive unpause + snapshot persist. First-class proxy; no-ops
        # on backends without idle machinery.
        if live_session is not None:
            try:
                await live_session.stop_idle_watcher()
            except Exception:
                pass

        await teardown_session(
            live_session,
            live_services,
            discard_snapshot=get_discard_snapshot_on_exit(),
        )
        # Clear the published process-global slots on exit (teardown_session
        # deliberately leaves the handle alone so switches can replace it).
        set_sandbox_session(None)
        set_switch_context(None)
        from omniagents.core.runtime.sandbox_lifecycle import (
            set_switch_executor as _clear_switch_executor,
        )

        _clear_switch_executor(None)

    return 0


def serve_main(argv: Optional[List[str]] = None) -> int:
    """The reserved ``<prog> serve`` entry point. Returns an exit status."""
    parser = build_serve_parser()
    args = parser.parse_args(argv)
    os.environ.setdefault("UVICORN_NO_ACCESS_LOG", "1")
    try:
        return asyncio.run(_serve(args))
    except KeyboardInterrupt:
        return 0


# ---------------------------------------------------------------------------
# sandbox-host — turn this machine into a remote sandbox backend
# ---------------------------------------------------------------------------


def exec_port_payload(port: int, host: str) -> Dict[str, Any]:
    """The first-stdout-line handshake of ``<prog> sandbox-host``.

    The launcher reads the first stdout line as JSON and registers
    ``exec_port`` with its cloud relay; ``host`` is informational.
    """
    return {"exec_port": port, "host": host}


async def _run_sandbox_host(host: str, port: int, workspace: Optional[str]) -> None:
    import websockets

    from omniagents.core.sandbox.host_bridge import serve_host_connection

    async def handler(ws: Any) -> None:
        await serve_host_connection(ws, workspace_root=workspace)

    async with websockets.serve(handler, host, port, max_size=None) as server:
        bound = next(iter(server.sockets)).getsockname()
        sys.stdout.write(json.dumps(exec_port_payload(bound[1], host)) + "\n")
        sys.stdout.flush()
        await asyncio.Future()  # run forever


def sandbox_host_main(argv: Optional[List[str]] = None) -> int:
    """The reserved ``<prog> sandbox-host`` entry point.

    The host (laptop) half of "computer-as-sandbox": the agent runs
    elsewhere (a cloud ``<prog> serve``); this process exposes the sandbox
    surface — exec / file IO / PTYs / exposed ports — over a WebSocket so
    a cloud ``HostBridgeSandboxSession`` can drive *this* machine's
    filesystem. The wire protocol + per-connection session handling live
    in :func:`omniagents.core.sandbox.host_bridge.serve_host_connection`
    (co-located with the cloud client so the protocol is defined once);
    this is just the CLI: bind a loopback WebSocket, hand each connection
    to that handler, and print ``{"exec_port": N}`` on stdout so the
    launcher can register the port with its cloud relay. One long-lived
    server serves many sessions (one per connection).
    """
    from omniagents.core.product import product_info

    parser = argparse.ArgumentParser(prog=f"{product_info().prog} sandbox-host")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Port to bind (0 = auto; the chosen port is printed as JSON).",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="Local workspace dir to root every session at (overrides the "
        "cloud-sent manifest root, which reflects the cloud's paths).",
    )
    args = parser.parse_args(argv)
    try:
        asyncio.run(_run_sandbox_host(args.host, args.port, args.workspace))
    except KeyboardInterrupt:
        return 0
    return 0
