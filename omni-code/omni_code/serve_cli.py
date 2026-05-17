"""``omni serve`` — host-side agent server backed by a long-lived SandboxSession.

Replaces the long-running container that ``omni sandbox`` currently spins
up. The agent runs on the host; its filesystem/shell tools route through
``omniagents.workspace`` which is bound to a ``SandboxWorkspace`` wrapping
the live session (via ``WorkspaceBindingCapability``).

Sandbox shape (image, manifest, services, exposed ports) comes from a
sandbox **profile** — a YAML document resolved through the layered chain
in :mod:`omni_code.sandbox_profile`. The launcher writes those profiles;
omni-code reads them.

Output: a single JSON line to stdout matching the launcher's
``SandboxJsonPayload``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import tarfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from omni_code.sandbox_profile import (
    ResolvedProfile,
    ServiceSpec,
    resolve_profile,
)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="omni serve",
        description=(
            "Run the Omni Code agent server on the host with a long-lived "
            "SandboxSession. Sandbox shape is read from the resolved profile "
            "chain (built-in default ← user-default ← per-project ← --profile). "
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
            "git-remote → ``repoUrl``, optional ``ref``."
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
            "Snapshot keying is now driven by --session-id, not this."
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
        "--profile",
        default=None,
        type=Path,
        help=(
            "Explicit profile YAML; bypasses user/project layers (tests, "
            "scripting, debugging — the launcher should not pass this)."
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
        help="Path to project.yml (default: omni-code's bundled spec).",
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
    if explicit is not None:
        return explicit.resolve()
    pkg_dir = Path(__file__).resolve().parent
    common_root = pkg_dir.parent
    repo_root_project = common_root / "project.yml"
    pkg_project = pkg_dir / "project.yml"
    if repo_root_project.is_file():
        return repo_root_project
    if pkg_project.is_file():
        return pkg_project
    raise FileNotFoundError(
        "Could not locate project.yml. Pass --agent-project explicitly."
    )


# ---------------------------------------------------------------------------
# Profile → SDK objects
# ---------------------------------------------------------------------------


def _build_sandbox(
    profile: ResolvedProfile,
    *,
    sources: List[Dict[str, Any]],
) -> Tuple[Any, Any, Any]:
    """Translate a resolved profile + project source descriptors into
    ``(client, options, manifest)``.

    Each entry in ``sources`` describes one repo/dir to mount under
    ``/workspace/<mountName>``. Workspace seeding is injected here
    (per-source manifest entries), not by the profile YAML — profile =
    container shape; workspace = per-project concern.
    """
    from agents.sandbox.manifest import User
    from omniagents.core.sandbox.factory import (
        build_manifest,
        build_sandbox_client,
    )

    client, options = build_sandbox_client(profile.raw)
    manifest = build_manifest(profile.raw)

    # Inject one seeding entry per source, keyed by mountName so the
    # SDK materializes each under /workspace/<mountName>.
    seen_mounts: set[str] = set()
    new_entries = dict(manifest.entries)
    for src in sources:
        mount_name = src.get("mountName")
        if not isinstance(mount_name, str) or not mount_name:
            raise ValueError(f"--source missing mountName: {src!r}")
        if mount_name in seen_mounts:
            raise ValueError(f"duplicate --source mountName: {mount_name!r}")
        seen_mounts.add(mount_name)
        seed_entry = _build_seed_entry(src)
        if seed_entry is not None:
            new_entries[mount_name] = seed_entry
    if new_entries != manifest.entries:
        manifest = manifest.model_copy(update={"entries": new_entries}, deep=True)

    # Pre-declare the profile's ``run_as`` identity on the session's
    # initial manifest. The SDK's :class:`SandboxRuntimeSessionManager`
    # injects ``User(name=run_as)`` into the agent's run-time manifest on
    # every ``Runner.run`` (via :meth:`_manifest_with_run_as_user`), then
    # validates that the running session's ``manifest.users`` matches.
    # Without this pre-declaration the first ``Runner.run`` raises
    # ``ValueError: Running injected sandbox sessions do not support
    # capability changes to manifest.users``. Declaring the user here
    # makes the SDK's idempotency check short-circuit (it sees the user
    # is already present) and the comparison passes.
    profile_run_as = profile.raw.get("run_as")
    if isinstance(profile_run_as, str) and profile_run_as:
        manifest = manifest.model_copy(
            update={"users": [*manifest.users, User(name=profile_run_as)]},
            deep=True,
        )

    return client, options, manifest


def _build_seed_entry(src: Dict[str, Any]) -> Any:
    """Construct the manifest entry that seeds /workspace/<mountName>
    from a single source descriptor dict.

    Accepted shapes (all require ``kind`` + ``mountName``):

    * ``{"kind": "local",       "path": str,    "mountName": str}``
    * ``{"kind": "local-git",   "path": str,    "mountName": str}``
    * ``{"kind": "git-remote",  "repoUrl": str, "mountName": str, "ref"?: str}``

    Returns ``None`` for unrecognised kinds (the dispatch in
    :func:`_build_sandbox` raises before reaching here for
    duplicates / missing mountNames, so callers don't see ``None``
    in normal flow).
    """
    from agents.sandbox.entries import GitRepo
    from omniagents.core.sandbox import LocalGitArchive, PermissiveLocalDir

    kind = src.get("kind")
    if kind == "local-git":
        path = src.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError(f"--source kind=local-git requires 'path': {src!r}")
        return LocalGitArchive(src=Path(path))
    if kind == "local":
        path = src.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError(f"--source kind=local requires 'path': {src!r}")
        return PermissiveLocalDir(src=Path(path))
    if kind == "git-remote":
        url = src.get("repoUrl")
        if not isinstance(url, str) or not url:
            raise ValueError(f"--source kind=git-remote requires 'repoUrl': {src!r}")
        ref = src.get("ref") or "HEAD"
        host, repo = _parse_git_remote_url(url)
        return GitRepo(host=host, repo=repo, ref=ref)
    raise ValueError(f"unknown --source kind: {kind!r}")


def _build_snapshot(
    *,
    snapshot_dir: Optional[Path],
    session_id: str,
) -> Any:
    """Return a configured ``LocalSnapshot`` or ``None``.

    A snapshot is configured only when *snapshot_dir* is set. The
    snapshot id is the caller-supplied (or auto-generated) session id,
    which is the stable identifier for this resumable workspace. The
    next ``client.create()`` with the same session id restores the
    prior workspace state instead of re-running the seed entry.
    """
    if snapshot_dir is None:
        return None
    from agents.sandbox.snapshot import LocalSnapshot

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    return LocalSnapshot(id=session_id, base_path=snapshot_dir)


def _safe_symlink_target(rel_path: str, target: str) -> bool:
    """Return True if a symlink entry would pass the SDK's hydrate
    validator (``allow_external_symlink_targets=False``).

    Absolute targets are rejected. Relative targets are rejected if
    they normalize outside the archive root via ``..``. Mirrors
    ``agents.sandbox.util.tar_utils._validate_symlink_target``.
    """
    target_path = PurePosixPath(target)
    if target_path.is_absolute():
        return False
    member_parent = PurePosixPath(rel_path).parent
    normalized: List[str] = []
    for part in (member_parent / target_path).parts:
        if part in ("", ".", "/"):
            continue
        if part == "..":
            if not normalized:
                return False
            normalized.pop()
            continue
        normalized.append(part)
    return True


def _scrub_session_snapshot(session: Any) -> None:
    """Drop unsafe symlinks from the just-persisted snapshot tar.

    The SDK's ``unix_local.persist_workspace`` does an unfiltered
    ``tar.add`` of the host workspace directory — so stale absolute
    symlinks on disk (e.g. a leftover ``.fvm/flutter_sdk →
    /Users/.../fvm/...`` from another machine) end up in the snapshot.
    On the next hydrate (cross-profile resume, or even same-profile
    resume into a fresh container) the SDK's validator throws
    ``WorkspaceArchiveWriteError`` and the session refuses to start.

    The seed path's ``LocalGitArchive`` already filters at write time
    in the opposite direction. This is the symmetric scrub on the
    persist side: same predicate, applied to the tar on disk before
    the next start has a chance to trip on it.

    Best-effort: silently no-ops if the snapshot path can't be
    determined or the tar can't be read. Any unsafe content the
    scrub fails to drop will still get caught by the validator on
    restore (with the original error message).
    """
    snapshot = getattr(getattr(session, "state", None), "snapshot", None)
    base_path = getattr(snapshot, "base_path", None)
    snap_id = getattr(snapshot, "id", None)
    if base_path is None or snap_id is None:
        return  # NoopSnapshot, RemoteSnapshot, or unrecognized shape
    snapshot_path = Path(base_path) / f"{snap_id}.tar"
    if not snapshot_path.exists():
        return

    tmp_path = snapshot_path.with_name(snapshot_path.name + ".scrub")
    dropped = 0
    try:
        with tarfile.open(snapshot_path, "r") as src:
            with tarfile.open(tmp_path, "w") as dst:
                for member in src:
                    if member.issym() and not _safe_symlink_target(
                        member.name, member.linkname
                    ):
                        dropped += 1
                        continue
                    data = src.extractfile(member) if member.isfile() else None
                    dst.addfile(member, data)
        os.replace(tmp_path, snapshot_path)
        if dropped:
            print(
                f"[teardown] scrubbed {dropped} unsafe symlink(s) from snapshot",
                file=sys.stderr,
            )
    except Exception as e:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"[teardown] snapshot scrub failed: {e}", file=sys.stderr)


def _parse_git_remote_url(url: str) -> Tuple[str, str]:
    """Split ``https://github.com/owner/name(.git)?`` into ``(host, owner/name)``.

    Also handles ``git@github.com:owner/name.git`` ssh form. Anything else
    raises — the launcher is expected to give us a clean URL.
    """
    cleaned = url.strip()
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    if cleaned.startswith("https://") or cleaned.startswith("http://"):
        scheme, rest = cleaned.split("://", 1)
        if "/" not in rest:
            raise ValueError(f"unparseable git URL (no path): {url!r}")
        host, path = rest.split("/", 1)
        return host, path
    if "@" in cleaned and ":" in cleaned and not cleaned.startswith("/"):
        # git@github.com:owner/name
        _user, hostpath = cleaned.split("@", 1)
        host, path = hostpath.split(":", 1)
        return host, path
    raise ValueError(f"unrecognized git URL form: {url!r}")


def _load_spec(project_path: Path, agent_key: Optional[str]) -> Any:
    from omniagents.core.project.runtime import (
        build_spec_from_project,
        load_project,
    )

    proj = load_project(str(project_path))
    return build_spec_from_project(proj, agent_key=agent_key)


def _attach_session_to_spec(spec: Any, session: Any, profile_raw: Dict[str, Any]) -> None:
    """Wire the long-lived SandboxSession into the spec so every Runner.run
    reuses it, and flip ``use_sandbox_agent`` so the builder constructs a
    SandboxAgent with ``WorkspaceBindingCapability`` prepended.

    The profile's top-level ``run_as`` (if set) flows into ``sandbox_run_as``
    so the builder constructs the workspace binding with a non-root user
    default — important for in-container security (the bash tool would
    otherwise run as root and could ``iptables -F`` the network allowlist).
    """
    spec.sandbox_use_sandbox_agent = True

    profile_run_as = profile_raw.get("run_as")
    if isinstance(profile_run_as, str) and profile_run_as:
        spec.sandbox_run_as = profile_run_as

    existing = getattr(spec, "sandbox_run_config", None)
    if existing is None:
        from agents.run_config import SandboxRunConfig

        spec.sandbox_run_config = SandboxRunConfig(session=session)
    else:
        existing.session = session


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


async def _run_init(session: Any, commands: List[str]) -> None:
    """Run each init command synchronously via ``session.exec`` as root.

    Init commands are one-shot setup hooks that fill in for the image's
    ENTRYPOINT (which the SDK bypasses). They typically do user remap,
    daemon startup, gitconfig persistence — all root-only operations.
    Images that set ``USER <non-root>`` would otherwise execute these as
    the non-privileged user and fail silently part-way through (the
    classic entrypoint scripts don't use ``set -e``), so we force root
    explicitly here regardless of the image's default user.

    Fails hard on the first non-zero exit, including the failing command
    and stderr in the raised exception.
    """
    for cmd in commands:
        result = await session.exec(cmd, shell=True, user="root")
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        # Always log init stdout/stderr so silent-fail entrypoints are
        # diagnosable. Trim to last ~2KB each so the launcher's IPC stream
        # doesn't choke.
        if stdout:
            print(f"[init stdout] {stdout[-2000:]}", file=sys.stderr)
        if stderr:
            print(f"[init stderr] {stderr[-2000:]}", file=sys.stderr)
        if result.exit_code != 0:
            raise RuntimeError(
                f"init command failed (exit {result.exit_code}): {cmd}\n"
                f"stderr: {stderr or '(empty)'}"
            )


async def _start_services(
    session: Any, services: List[ServiceSpec]
) -> List[Tuple[ServiceSpec, int]]:
    """Launch each service via ``pty_exec_start``. Hard-fail on immediate exit."""
    started: List[Tuple[ServiceSpec, int]] = []
    for svc in services:
        # Per-service env is materialized as a `KEY=val` prefix on the shell
        # command. ``session.pty_exec_start`` doesn't have a separate env=
        # parameter (env vars are a manifest concern), so we use the same
        # shape ``omniagents.workspace.SandboxWorkspace.exec`` uses internally.
        cmd = svc.command
        if svc.env:
            prefix = " ".join(
                f"{k}={_shell_quote(v)}" for k, v in svc.env.items()
            )
            cmd = f"{prefix} {cmd}"

        update = await session.pty_exec_start(
            cmd,
            shell=True,
            tty=False,
            user=svc.user,
            yield_time_s=0.0,
        )
        if update.exit_code is not None:
            raise RuntimeError(
                f"service {svc.name!r} exited immediately with code "
                f"{update.exit_code}; command: {cmd}"
            )
        if update.process_id is None:
            raise RuntimeError(
                f"service {svc.name!r} did not return a process_id; cannot track"
            )
        started.append((svc, update.process_id))
    return started


def _shell_quote(value: str) -> str:
    # Lightweight quoting for env=val prefixes; shlex.quote is overkill for
    # the values we accept here (strings without embedded newlines).
    if not value or any(c in value for c in " \t\"'$`\\"):
        return "'" + value.replace("'", "'\\''") + "'"
    return value


async def _resolve_service_urls(
    session: Any, started: List[Tuple[ServiceSpec, int]]
) -> Dict[str, str]:
    """For services that declared a port, ask the SDK for the host endpoint."""
    out: Dict[str, str] = {}
    for svc, _pid in started:
        if svc.port is None:
            continue
        endpoint = await session.resolve_exposed_port(svc.port)
        # ExposedPortEndpoint has .host and .port; build an http URL.
        # Future: let services declare scheme=ws|https if needed.
        out[svc.name] = f"http://{endpoint.host}:{endpoint.port}"
    return out


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


async def _serve(args: argparse.Namespace) -> int:
    # --- Profile resolution ---
    try:
        profile = resolve_profile(
            workspace_dir=args.workspace,
            project_id=args.project,
            explicit_path=args.profile,
        )
    except (FileNotFoundError, ValueError) as e:
        print(json.dumps({"error": f"profile: {e}"}), file=sys.stderr)
        return 1

    # --- Sandbox session ---
    # Parse the repeated --source JSON descriptors. Empty list = no
    # seeding (container boots with an empty /workspace).
    try:
        sources_parsed: List[Dict[str, Any]] = []
        for raw in args.sources:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"--source value is not valid JSON: {raw!r}") from e
            if not isinstance(parsed, dict):
                raise ValueError(f"--source must be a JSON object, got {parsed!r}")
            sources_parsed.append(parsed)
        client, options, manifest = _build_sandbox(profile, sources=sources_parsed)
    except ValueError as e:
        print(json.dumps({"error": f"source: {e}"}), file=sys.stderr)
        return 1

    # Publish the resolved manifest root so server functions can report the
    # path the agent's tools actually see (e.g. ``/workspace`` for docker)
    # instead of falling back to the host process cwd.
    from omni_code.agent_workspace_state import set_manifest_root

    set_manifest_root(str(manifest.root) if manifest.root is not None else None)

    # --- Session id + snapshot config ---
    # The session id is the stable identifier for this resumable
    # workspace. Caller-supplied via --session-id (resume case) or
    # auto-generated here (first start). Emitted in the readiness
    # payload so the launcher can capture and persist it for next time.
    session_id = args.session_id or str(uuid.uuid4())
    snapshot = _build_snapshot(
        snapshot_dir=args.snapshot_dir,
        session_id=session_id,
    )

    session = await client.create(
        manifest=manifest,
        options=options,
        snapshot=snapshot,
    )
    await session.start()

    # --- Init (synchronous, fail-hard) ---
    started_services: List[Tuple[ServiceSpec, int]] = []
    try:
        await _run_init(session, profile.init)
        started_services = await _start_services(session, profile.services)
        service_urls = await _resolve_service_urls(session, started_services)
    except Exception:
        await _teardown(session, started_services)
        raise

    # --- Agent spec + session injection ---
    agent_project_path = _resolve_agent_project_path(args.agent_project)
    spec = _load_spec(agent_project_path, args.agent)
    _attach_session_to_spec(spec, session, profile.raw)

    # --- FastAPI app + uvicorn ---
    from omniagents.backends.server.app import build_app
    import uvicorn

    app = build_app(
        config_path=str(agent_project_path),
        spec=spec,
        auth_token=args.auth_token,
    )
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
    base_url = f"http://{args.host}:{port}"
    ws_url = f"ws://{args.host}:{port}/ws"
    if args.auth_token:
        ws_url = f"{ws_url}?token={args.auth_token}"
    # Pull container_id from session.state when present (docker backends
    # expose it; unix_local / e2b / etc. don't). The launcher uses this
    # for ``docker exec``-based operations like PR diff extraction.
    container_id = getattr(session.state, "container_id", None)
    payload = {
        "sandbox_url": base_url,
        "ws_url": ws_url,
        "ui_url": base_url,
        "services": service_urls,
        "ports": {"ui": port},
        "container_id": container_id,
        "_debug": {
            "client": (profile.raw.get("client") or {}).get("type"),
            "manifest_root": getattr(session.state.manifest, "root", None),
            "profile_sources": [str(s) for s in profile.sources],
        },
    }
    if args.output == "json":
        print(json.dumps(payload), flush=True)
    else:
        backend = (profile.raw.get("client") or {}).get("type", "?")
        print(f"Omni Code: {base_url}  (profile backend={backend})", flush=True)

    # --- Run server + clean SIGTERM ---
    def _request_stop(*_: object) -> None:
        server.should_exit = True

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_a: _request_stop())

    try:
        await server.serve()
    finally:
        await _teardown(session, started_services)

    return 0


async def _teardown(
    session: Any, started_services: List[Tuple[ServiceSpec, int]]
) -> None:
    # pty_terminate_all is broad but appropriate — every PTY in this session
    # was started by us.
    if started_services:
        try:
            await session.pty_terminate_all()
        except Exception:
            pass
    # ``session.stop()`` persists the workspace snapshot internally
    # (``_persist_snapshot`` → ``snapshot_lifecycle.persist_snapshot``)
    # for any non-noop snapshot configuration; the SDK does not expose
    # a separate public ``persist_snapshot`` method on the session.
    # Snapshot is best-effort: log any failure so the launcher can
    # surface it, but don't block shutdown.
    try:
        await session.stop()
    except Exception as e:
        print(f"[teardown] session stop failed: {e}", file=sys.stderr)
    _scrub_session_snapshot(session)
    try:
        await session.aclose()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    os.environ.setdefault("UVICORN_NO_ACCESS_LOG", "1")
    try:
        code = asyncio.run(_serve(args))
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)
