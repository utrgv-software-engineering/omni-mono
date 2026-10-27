"""Reserved ``<prog> serve`` — the launcher contract.

Any product with a ``product:`` block in its project.yml gets a
launcher-compatible agent server from convention: ``<prog> serve`` brings
up a long-lived sandbox session (or the no-sandbox ``host`` target)
against the product's layered profile chain, runs the product's
entrypoint agent over it behind FastAPI/uvicorn, and emits the single
JSON readiness line the desktop launcher parses.

It is mounted as a reserved subcommand by
:mod:`omniagents.product_cli`; a product may still override it via its
``cli.extensions`` ``COMMANDS`` mapping, but the defaults here are
launcher-complete. The wire contract (spawn argv, readiness payload,
error protocol) is versioned as :data:`SERVE_PROTOCOL_VERSION` and
documented in ``docs/serve-protocol.md`` — that document is what the
launcher targets; treat any change to the payload or flags as a protocol
revision.

Lifted from omni-code's ``serve_cli.py``; the
session lifecycle itself lives in
:mod:`omniagents.core.runtime.serve_harness`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import signal
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Version of the serve wire contract (spawn argv + readiness payload +
#: error protocol) documented in
#: ``docs/serve-protocol.md``. Reported by ``<prog> describe`` so the
#: launcher can verify compatibility before spawning.
SERVE_PROTOCOL_VERSION = 3

__all__ = [
    "SERVE_PROTOCOL_VERSION",
    "build_serve_parser",
    "build_readiness_payload",
    "serve_main",
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
        description="Run a targetless AgentHost server.",
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
        "--agent-host-control-token",
        default=None,
        help=(
            "Distinct admin bearer token for the trusted AgentHost controller. "
            "It is not included in the readiness payload."
        ),
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
        "--exit-on-stdin-close",
        action="store_true",
        help=(
            "Shut down when stdin reaches EOF. A launcher holds the pipe open "
            "for the child's lifetime, so the server exits with its launcher "
            "even when the launcher crashes."
        ),
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


def _watch_stdin_eof(on_eof: Any, fd: int = 0) -> None:
    """Call *on_eof* once the launcher's stdin pipe reaches EOF.

    The watcher reads a private duplicate of *fd*, and *fd* itself is pointed
    at the null device, so subprocesses serve spawns without an explicit stdin
    see an empty input instead of inheriting the never-written launcher pipe
    (a child that reads stdin would otherwise block forever).
    """
    import threading

    watched = os.dup(fd)
    null = os.open(os.devnull, os.O_RDONLY)
    try:
        os.dup2(null, fd)
    finally:
        os.close(null)

    def _read() -> None:
        with contextlib.suppress(OSError):
            while os.read(watched, 4096):
                pass
        with contextlib.suppress(OSError):
            os.close(watched)
        try:
            on_eof()
        except RuntimeError:
            pass  # the event loop already closed: serve is exiting anyway

    threading.Thread(target=_read, name="stdin-eof-watch", daemon=True).start()


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

    proj = load_project(str(project_path))
    spec = build_spec_from_project(proj, agent_key=agent_key)
    spec.project_context = proj
    return spec


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
    agent_host_id: str,
) -> Dict[str, Any]:
    """Build the targetless AgentHost readiness handshake."""
    from omniagents.rpc.auth import legacy_query_auth_enabled

    base_url = f"http://{host}:{port}"
    ws_url = f"ws://{host}:{port}/ws"
    if auth_token and legacy_query_auth_enabled(None):
        # Deprecated migration path: launchers that predate header/ticket
        # auth still consume the token from the ws_url query string. New
        # launchers read ``auth_token`` below and send it as an
        # ``Authorization: Bearer`` header (or mint a /auth/ws-ticket).
        ws_url = f"{ws_url}?token={auth_token}"
    return {
        "sandbox_url": base_url,
        "ws_url": ws_url,
        "ui_url": base_url,
        # Dedicated credential field: consumers authenticate with an
        # ``Authorization: Bearer`` header (or POST /auth/ws-ticket for
        # browser WebSockets) instead of a query-string token.
        "auth_token": auth_token,
        "ports": {"ui": port},
        "agent_host_id": agent_host_id,
    }


def _parse_json_flag_list(raw_values: List[str], flag: str) -> List[Dict[str, Any]]:
    """Parse a repeated JSON-descriptor flag such as ``--credential``."""
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
        _cancel_active_runs,
    )
    from omniagents.core.sandbox.profiles import resolve_product_profile

    # --- Codex (ChatGPT OAuth) provider ---
    # Register the openai-oauth provider builder so the main agent can use a
    # Codex model whenever one is selected (default or switched mid-session) —
    # not only when it's the startup default. Aux agents (compact, recap,
    # summarizer) build their own via build_model_provider's openai-oauth
    # branch. Safe + idempotent regardless of sign-in state.
    _register_codex_provider()

    # Parse the repeated --credential JSON descriptors (boot-time git/CLI auth).
    try:
        credentials_parsed = _parse_json_flag_list(args.credentials, "--credential")
    except ValueError as e:
        print(json.dumps({"error": f"credential: {e}"}), file=sys.stderr)
        return 1

    # --- Agent definition ---
    agent_project_path = _resolve_agent_project_path(args.agent_project)
    spec = _load_spec(agent_project_path, args.agent)

    # --- FastAPI app + uvicorn ---
    import uvicorn

    from omniagents.backends.server.app import build_app
    from omniagents.core.utils.serving import ReadyServer, bind_socket

    # The listening socket is bound first (port 0 → the kernel picks) and
    # handed to uvicorn, so the port in the readiness payload is the one the
    # server holds — and a taken port fails here, before anything needs
    # tearing down. The payload is printed once uvicorn has started, so a
    # client that dials on reading it finds the server accepting.
    try:
        sock = bind_socket(args.host, args.port or 0)
    except OSError as e:
        print(
            json.dumps({"error": f"bind {args.host}:{args.port}: {e}"}),
            file=sys.stderr,
        )
        return 1
    port = sock.getsockname()[1]
    from omniagents.core.runtime.environment_provisioner import (
        ServeEnvironmentProvisioner,
    )
    from omniagents.execution import AgentHostResources

    agent_host_id = f"agenthost_{uuid.uuid4().hex}"
    agent_host_resources = AgentHostResources.create(agent_host_id)
    app = build_app(
        config_path=str(agent_project_path),
        spec=spec,
        auth_token=args.auth_token,
        agent_host_resources=agent_host_resources,
        agent_host_control_token=args.agent_host_control_token,
    )

    def _resolve_provisioned_profile(request, workspace_dir):
        definition = agent_host_resources.provisioning.profile(
            request.profile_id,
            owner_user_id=request.workspace.owner_user_id,
        )
        return resolve_product_profile(
            workspace_dir=workspace_dir,
            project_id=definition.get("project_id"),
            explicit_path=(
                Path(definition["path"]) if definition["kind"] == "path" else None
            ),
        )

    agent_host_resources.environments.set_provisioner(
        ServeEnvironmentProvisioner(
            profile_resolver=_resolve_provisioned_profile,
            workspace_directory_resolver=(
                agent_host_resources.provisioning.workspace_directory
            ),
            snapshot_dir=args.snapshot_dir,
            credentials=credentials_parsed,
        )
    )

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

    config = uvicorn.Config(
        app,
        log_level="info",
        access_log=False,
    )

    # --- Readiness payload ---
    payload = build_readiness_payload(
        host=args.host,
        port=port,
        auth_token=args.auth_token,
        agent_host_id=agent_host_id,
    )

    def _announce_ready(_server: ReadyServer) -> None:
        if args.output == "json":
            # ``default=str`` coerces any stray non-JSON value (e.g. a PosixPath —
            # manifest.root can arrive as one via pydantic ``model_copy(update=)``,
            # which skips the ``str`` field coercion) to its string form. The
            # readiness payload is the launcher's handshake; it must never fail to
            # serialize and crash serve. str(PosixPath("/workspace")) == "/workspace",
            # so the launcher still receives the value it expects.
            print(json.dumps(payload, default=str), flush=True)
        else:
            label = None
            try:
                from omniagents.core.product import product_info

                label = product_info().label
            except Exception:
                pass
            print(f"{label or 'Agent server'}: {payload['ui_url']}", flush=True)

    server = ReadyServer(config, on_started=_announce_ready)

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

    if args.exit_on_stdin_close:
        _watch_stdin_eof(lambda: loop.call_soon_threadsafe(_request_stop))

    try:
        await server.serve(sockets=[sock])
    finally:
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

        # Stop the auto-recap watcher (cancels its task + detaches listeners).
        recap_scheduler_to_stop = getattr(app.state, "recap_scheduler", None)
        if recap_scheduler_to_stop is not None:
            try:
                await recap_scheduler_to_stop.stop()
            except Exception:
                pass
        # The AgentHost owns every execution environment, including any that
        # were provisioned after startup. Shut them all down through the same
        # manager/controller boundary used by explicit control-plane stops.
        try:
            await app.state.agent_service.environment_manager.shutdown()
        except Exception as exc:  # shutdown remains best-effort on process exit
            print(
                json.dumps({"warning": f"environment shutdown failed: {exc}"}),
                file=sys.stderr,
            )
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
