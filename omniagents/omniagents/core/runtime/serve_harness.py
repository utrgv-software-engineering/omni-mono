"""Serve-session harness: bring an agent's sandbox session up against a
profile, keep it healthy, and tear it down.

This is the generic core of a product's ``<cli> serve`` runtime, lifted
from omni-code's ``serve_cli.py``. The product keeps the CLI surface,
spec loading, FastAPI/uvicorn wiring, and the readiness protocol; the
harness owns everything between "profile resolved" and "live session
attached to the running agent":

* :func:`bring_up_session` — build the sandbox client from a
  :class:`~omniagents.core.sandbox.profiles.ResolvedProfile`,
  reattach to the workspace's recorded container (docker) or create a
  fresh session, run init commands (fresh workspaces only), start
  services, and resolve their URLs.
* :func:`teardown_session` — detach from a durable container session
  (leaving it running), or stop + destroy it on terminal shutdown.
  Idempotent and ``None``-safe.
Runtime placement is returned to the AgentHost for explicit environment
registration. The harness does not publish filesystem or sandbox handles in
process-global state.

Product knobs, defaulting to the omni-code values they were lifted with:

* ``NOOP_SNAPSHOT_CLIENT_TYPES`` — client types whose lifecycle snapshot
  is a noop (the workspace already lives on durable host storage).
* ``DEFAULT_SERVICE_URL_SUFFIXES`` — per-service-name URL suffixes
  applied when resolving exposed-port endpoints (noVNC's client page for
  a service named ``vnc``).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import (
    Any,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from omniagents.core.sandbox.profiles import (
    ResolvedProfile,
    ServiceSpec,
    TerminalSpec,
)

__all__ = [
    "BroughtUpSession",
    "bring_up_session",
    "teardown_session",
    "write_session_state",
    "NOOP_SNAPSHOT_CLIENT_TYPES",
    "DEFAULT_SERVICE_URL_SUFFIXES",
]


# Client types whose lifecycle snapshot is a noop: the workspace is
# durable without a tar archive — a host directory for ``unix_local``,
# the remote host's disk for ``host_bridge``, and the container's own
# writable layer for ``docker`` (sessions reattach to their recorded
# container instead of rehydrating an archive).
NOOP_SNAPSHOT_CLIENT_TYPES: Tuple[str, ...] = ("unix_local", "docker", "host_bridge")

# Per-service-name URL suffixes applied by :func:`_resolve_service_urls`.
# A service named ``vnc`` is assumed to be noVNC and gets pointed at its
# client page. Products with different conventions pass their own map.
DEFAULT_SERVICE_URL_SUFFIXES: Mapping[str, str] = {
    "vnc": "/vnc.html?autoconnect=true&resize=scale",
}


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
    * ``{"kind": "git-remote",  "repoUrl": str, "mountName": str, "ref"?: str,
         "auth"?: {"tokenEnv": str, "username": str}}``

    A ``git-remote`` source carrying ``auth`` is a *private* repo: ``tokenEnv``
    names the environment variable (injected by the launcher into this process)
    holding the token, and the entry becomes an ``AuthenticatedGitRepo`` that
    configures an in-container git credential helper. Without ``auth`` it's a
    public-repo ``PartialCloneGitRepo`` clone. Both clone blobless
    (``--filter=blob:none``) rather than shallow, so the workspace repo has
    full history, all remote refs, and a normal wildcard fetch refspec.

    Returns ``None`` for unrecognised kinds (the dispatch in
    :func:`_build_sandbox` raises before reaching here for
    duplicates / missing mountNames, so callers don't see ``None``
    in normal flow).
    """
    from omniagents.core.sandbox import (
        AuthenticatedGitRepo,
        LocalGitArchive,
        PartialCloneGitRepo,
        PermissiveLocalDir,
    )

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
        auth = src.get("auth")
        if isinstance(auth, dict) and auth.get("tokenEnv"):
            return AuthenticatedGitRepo(
                host=host,
                repo=repo,
                ref=ref,
                username=auth.get("username") or "x-access-token",
                token_env=auth["tokenEnv"],
            )
        return PartialCloneGitRepo(host=host, repo=repo, ref=ref)
    raise ValueError(f"unknown --source kind: {kind!r}")


def _build_snapshot(
    *,
    snapshot_dir: Optional[Path],
    snapshot_ref: str,
    client_type: Optional[str],
    no_snapshot_client_types: Sequence[str] = NOOP_SNAPSHOT_CLIENT_TYPES,
) -> Any:
    """Return a configured ``LocalSnapshot`` or ``None``.

    A snapshot is configured only when *snapshot_dir* is set AND the
    sandbox client has an ephemeral workspace. For ``unix_local`` the
    workspace IS a host directory; ``persist_workspace`` would just tar
    the host dir and ``hydrate_workspace`` would untar it back into the
    same path on resume — pure overhead, with no state to preserve that
    isn't already sitting on disk. Host mode therefore falls through to
    a noop snapshot regardless of ``--snapshot-dir``, which also sidesteps
    the unsafe-symlink accumulation that ``_scrub_workspace_snapshot``
    exists to mop up. ``host_bridge``: the workspace is a durable
    directory on the *remote host* (the user's laptop), reachable only
    via the exec channel. Tarring it back to the serve host's disk would
    both defeat the privacy intent and be pure overhead — same reasoning
    as ``unix_local``'s host directory. *no_snapshot_client_types* is
    the product knob for that set.

    The snapshot id is the Workspace's durable ``snapshot_ref``. A later
    environment materialization for that Workspace restores its prior files
    instead of relying on a container identity.
    """
    if snapshot_dir is None:
        return None
    if client_type in no_snapshot_client_types:
        return None
    from agents.sandbox.snapshot import LocalSnapshot

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    return LocalSnapshot(id=snapshot_ref, base_path=snapshot_dir)


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


def _scrub_workspace_snapshot(session: Any) -> None:
    """Drop unsafe symlinks from the persisted snapshot tar.

    The SDK's ``unix_local.persist_workspace`` does an unfiltered
    ``tar.add`` of the host workspace directory — so stale absolute
    symlinks on disk (e.g. a leftover ``.fvm/flutter_sdk →
    /Users/.../fvm/...`` from another machine) end up in the snapshot.
    On the next hydrate (cross-profile resume, or even same-profile
    resume into a fresh container) the SDK's validator throws
    ``WorkspaceArchiveWriteError`` and the session refuses to start.

    The seed path's ``LocalGitArchive`` already filters at write time
    in the opposite direction. This is the symmetric scrub on the
    hydrate side: same predicate, applied once by ``bring_up_session``
    to the tar on disk before ``start()`` has a chance to trip on it.
    (It used to also run on teardown; that was a redundant full
    read+rewrite of the tar, since every hydrate scrubs anyway.)

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


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


def _run_as_exec_user(profile_raw: Dict[str, Any]) -> Optional[str]:
    """The profile's ``run_as`` identity, used so credentials are written as the
    user the agent/terminal actually run as (a devbox runs tools as uid 1000;
    creds written as root would be invisible to ``gh``/``git`` at runtime).

    Passed verbatim to ``session.exec(user=...)``, which for the docker backend
    becomes ``docker exec --user`` — that accepts ``uid``/``uid:gid``/``name``
    forms (``"1000:1000"``), NOT the ``#uid`` sudo form. Missing/blank → ``None``
    (write as the session default)."""
    run_as = profile_raw.get("run_as")
    if not isinstance(run_as, str) or not run_as.strip():
        return None
    return run_as.strip()


async def _configure_credentials(
    session: Any, credentials: List[Dict[str, Any]], *, run_as_user: Optional[str]
) -> None:
    """Configure git + CLI auth (``gh`` / ``az devops``) inside the sandbox for
    each linked host the project uses, as ``run_as_user``.

    Token values never travel on argv: the descriptor carries only the env-var
    *name* (``tokenEnv``), read host-side here from the serve process env
    (where the launcher injected it). A credential whose token env is unset, or
    whose ``url`` can't be parsed, is skipped — best-effort, never fatal.

    Deduped so each host is configured once (git + ``gh`` are host-wide) while
    Azure DevOps still logs in per organization (``az devops login`` keys on org,
    and one PAT can span orgs).
    """
    if not credentials:
        return
    from omniagents.core.sandbox import configure_host_credentials

    seen: set[Any] = set()
    for cred in credentials:
        token_env = cred.get("tokenEnv")
        token = os.environ.get(token_env) if token_env else None
        if not token:
            continue
        url = cred.get("url")
        if not isinstance(url, str) or not url:
            continue
        try:
            host, repo = _parse_git_remote_url(url)
        except ValueError:
            # Unparseable remote (e.g. an SSH form we don't rewrite) — the git
            # credential helper keys on https hosts anyway, so skip.
            continue
        # Dedup key: per-org for dev.azure.com (one PAT, many orgs), else per-host
        # (github/gh and the git helper are host-wide; the visualstudio.com org is
        # in the hostname so host alone suffices).
        key = (host, repo.split("/", 1)[0]) if host == "dev.azure.com" else host
        if key in seen:
            continue
        seen.add(key)
        # Best-effort and isolated: credential setup must never block the sandbox
        # from coming up. A transport error / bad exec for one host is logged and
        # skipped so the agent still starts (just without that host's CLI auth).
        try:
            await configure_host_credentials(
                session,
                host=host,
                repo=repo,
                username=cred.get("username") or "x-access-token",
                token=token,
                user=run_as_user,
            )
        except Exception as e:  # noqa: BLE001 — never let cred setup fail boot
            print(f"[credentials] configuring {host} failed: {e}", file=sys.stderr)


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


# Sentinel process_id for a service that was already running when the
# environment materialized. We have no PTY pid for it; ``teardown_session``
# only checks ``started_services`` truthiness (never the pids) and
# ``_resolve_service_urls`` keys off ``svc.port``, so this is inert downstream.
_ADOPTED_SERVICE_PID = -1


async def _port_is_listening(session: Any, port: int) -> bool:
    """Best-effort check whether *port* already accepts TCP connections inside
    the container. Uses bash's ``/dev/tcp`` (always present in the devbox image)
    so we don't depend on ``ss``/``netstat`` being installed. Any error returns
    False — a failed probe just means we fall through and launch the service.
    """
    probe = f"exec 3<>/dev/tcp/127.0.0.1/{int(port)}"
    try:
        result = await session.exec(
            f"bash -c {_shell_quote(probe)} 2>/dev/null", shell=True, user="root"
        )
        return result.exit_code == 0
    except Exception:
        return False


async def _start_services(
    session: Any, services: List[ServiceSpec]
) -> List[Tuple[ServiceSpec, int]]:
    """Launch each service via ``pty_exec_start``. Hard-fail on immediate exit.

    Idempotent startup: profile init or an image entrypoint may already have
    started a declared service. Relaunching it would bind-clash (for example,
    code-server exits 1 with ``EADDRINUSE``), so a service that declares a port
    is probed first and an existing listener is adopted instead of duplicated.
    """
    started: List[Tuple[ServiceSpec, int]] = []
    for svc in services:
        # Adopt an already-running instance created by init or the image.
        if svc.port is not None and await _port_is_listening(session, svc.port):
            print(
                f"[services] {svc.name!r} already listening on port {svc.port}; "
                "adopting existing instance",
                file=sys.stderr,
            )
            started.append((svc, _ADOPTED_SERVICE_PID))
            continue

        # Per-service env is materialized as a `KEY=val` prefix on the shell
        # command. ``session.pty_exec_start`` doesn't have a separate env=
        # parameter (env vars are a manifest concern), so we use the same
        # shape ``omniagents.workspace.SandboxWorkspace.exec`` uses internally.
        cmd = svc.command
        if svc.env:
            prefix = " ".join(f"{k}={_shell_quote(v)}" for k, v in svc.env.items())
            cmd = f"{prefix} {cmd}"

        update = await session.pty_exec_start(
            cmd,
            shell=True,
            tty=False,
            user=svc.user,
            yield_time_s=0.0,
        )
        if update.exit_code is not None:
            # Surface the service's own output — without it a bare "exited with
            # code 1" hides the real cause (EADDRINUSE, missing binary, etc.).
            output = update.output.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"service {svc.name!r} exited immediately with code "
                f"{update.exit_code}; command: {cmd}\n"
                f"output: {output or '(empty)'}"
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
    session: Any,
    started: List[Tuple[ServiceSpec, int]],
    *,
    url_suffixes: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """For services that declared a port, ask the SDK for the host endpoint.

    *url_suffixes* maps service name → path/query suffix appended to the
    resolved base URL (default :data:`DEFAULT_SERVICE_URL_SUFFIXES`, which
    points a ``vnc`` service at its noVNC client page).
    """
    suffixes = DEFAULT_SERVICE_URL_SUFFIXES if url_suffixes is None else url_suffixes
    out: Dict[str, str] = {}
    for svc, _pid in started:
        if svc.port is None:
            continue
        endpoint = await session.resolve_exposed_port(svc.port)
        # ExposedPortEndpoint has .host and .port; build an http URL.
        # Future: let services declare scheme=ws|https if needed.
        url = f"http://{endpoint.host}:{endpoint.port}"
        suffix = suffixes.get(svc.name)
        if suffix:
            url = f"{url}{suffix}"
        out[svc.name] = url
    return out


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


@dataclass
class BroughtUpSession:
    """Result of bringing a sandbox session up: the live session plus the
    derived state the serve host consumes afterward."""

    session: Any
    started_services: List[Tuple[ServiceSpec, int]]
    service_urls: Dict[str, Any]
    manifest: Any
    client_type: Optional[str]
    container_id: Optional[str]
    workspace_preserved: bool = False


def _read_session_state(state_path: Path) -> Optional[Dict[str, Any]]:
    """Parse a persisted sandbox-state record; None when missing/corrupt."""
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_session_state(state_path: Path, client: Any, session: Any) -> None:
    """Persist the session's backend state so a later serve process can
    reattach to its container. Atomic (tmp + rename); best-effort — a
    failed write degrades to a fresh container next boot, never a crash.

    The top-level ``container_id`` duplicates the SDK payload's field so
    ownership tooling (the launcher's orphan sweep) can read it without
    knowing the SDK schema.
    """
    try:
        payload = {
            "version": 1,
            "backend": getattr(client, "backend_id", None) or type(client).__name__,
            "container_id": getattr(session.state, "container_id", None),
            "session_state": client.serialize_session_state(session.state),
        }
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_name(state_path.name + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(state_path)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[serve] failed to persist sandbox state to {state_path}: {exc}",
            file=sys.stderr,
        )


async def bring_up_session(
    *,
    profile: ResolvedProfile,
    sources_parsed: List[Dict[str, Any]],
    snapshot_ref: str,
    snapshot_dir: Optional[Path],
    credentials_parsed: Optional[List[Dict[str, Any]]] = None,
    state_path: Optional[Path] = None,
) -> BroughtUpSession:
    """Build the sandbox client from *profile* and attach a session.

    When *state_path* holds a prior session-state record (docker), the
    recorded container is resumed: its writable layer IS the durable
    workspace, so init commands are skipped (re-running them would move
    the ``omni/seed`` PR baseline) and already-listening services are
    adopted rather than respawned. Otherwise a fresh session is created
    and fully initialized. The state record is (re)written after a
    successful bring-up. Raises ``ValueError`` for a bad profile/source;
    tears the partial session down (re-raising) if init/services fail —
    destroying a fresh container, but leaving a resumed one intact for
    the next attempt.
    """
    client, options, manifest = _build_sandbox(profile, sources=sources_parsed)

    client_type_raw = (profile.raw.get("client") or {}).get("type")
    client_type = client_type_raw if isinstance(client_type_raw, str) else None
    snapshot = _build_snapshot(
        snapshot_dir=snapshot_dir,
        snapshot_ref=snapshot_ref,
        client_type=client_type,
    )

    session: Any = None
    recorded_container_id: Optional[str] = None
    if state_path is not None and state_path.is_file() and hasattr(client, "resume"):
        record = _read_session_state(state_path)
        recorded_state = record.get("session_state") if record else None
        if isinstance(recorded_state, dict):
            try:
                session = await client.resume(
                    client.deserialize_session_state(recorded_state)
                )
                raw_id = record.get("container_id") if record else None
                recorded_container_id = raw_id if isinstance(raw_id, str) else None
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[serve] sandbox state {state_path} not resumable ({exc}); "
                    "creating a fresh session",
                    file=sys.stderr,
                )
                session = None

    if session is None:
        session = await client.create(
            manifest=manifest,
            options=options,
            snapshot=snapshot,
        )
        # Scrub the snapshot tar BEFORE start() hydrates it — this is the ONLY
        # scrub site. Without it, a persisted tar containing an unsafe symlink
        # (e.g. a committed `.fvm/flutter_sdk -> /Users/...` absolute symlink
        # in a cloned repo) would make `start()` throw
        # WorkspaceArchiveWriteError and the session would never come up.
        # No-op for noop-snapshot backends (docker/unix_local/host_bridge).
        # Off-loop: the scrub reads and rewrites the whole tar, and
        # bring_up_session runs on the shared serve loop.
        await asyncio.to_thread(_scrub_workspace_snapshot, session)

    await session.start()
    # Preserved = we resumed AND landed on the same container (resume falls
    # back to a fresh container when the recorded one is gone — that fresh
    # container was re-seeded from the manifest and needs full init).
    workspace_preserved = (
        recorded_container_id is not None
        and getattr(session.state, "container_id", None) == recorded_container_id
    )
    started_services: List[Tuple[ServiceSpec, int]] = []
    try:
        # Configure git + CLI auth before init/services and before the agent
        # runs, so private clones already done during create() are joined by
        # working `gh` / `az devops` for the agent's own commands. Written as the
        # profile's run_as identity so creds land in the HOME the agent uses.
        # Re-run on reattach too — tokens rotate between serve processes.
        await _configure_credentials(
            session,
            credentials_parsed or [],
            run_as_user=_run_as_exec_user(profile.raw),
        )
        if not workspace_preserved:
            await _run_init(session, profile.init)
        started_services = await _start_services(session, profile.services)
        service_urls = await _resolve_service_urls(session, started_services)
    except Exception:
        # A fresh container is half-initialized garbage — destroy it. A
        # preserved container holds real user work — detach and leave it
        # for the next attempt.
        await teardown_session(
            session,
            started_services,
            detach=workspace_preserved,
            destroy=not workspace_preserved,
            state_path=None if workspace_preserved else state_path,
        )
        raise

    if state_path is not None:
        write_session_state(state_path, client, session)

    return BroughtUpSession(
        session=session,
        started_services=started_services,
        service_urls=service_urls,
        manifest=manifest,
        client_type=client_type,
        container_id=getattr(session.state, "container_id", None),
        workspace_preserved=workspace_preserved,
    )


def _is_host_profile(profile_raw: Dict[str, Any]) -> bool:
    """True when the profile selects the ``host`` execution target — no
    sandbox session: plain Agent on a HostWorkspace, host PTY terminal."""
    return ((profile_raw.get("client") or {}).get("type")) == "host"


# ---------------------------------------------------------------------------
# In-place profile switching
# ---------------------------------------------------------------------------


def _profile_display_name(
    explicit_path: Optional[Any], profile: ResolvedProfile
) -> str:
    """Short label for a profile: the file stem when selected by path
    (``devbox.yml`` → ``devbox``), else the client backend type, else 'default'."""
    if explicit_path:
        return Path(str(explicit_path)).stem
    backend = (profile.raw.get("client") or {}).get("type")
    return backend if isinstance(backend, str) and backend else "default"


async def _cancel_active_runs(service: Any) -> None:
    """Cancel any in-flight run so the session swap lands between runs (the
    in-flight run holds its own per-run SandboxRuntime bound to the old
    session). Best-effort across every tracked run."""
    if service is None:
        return
    try:
        run_ids = service.active_run_ids()
    except Exception:  # noqa: BLE001
        return
    for run_id in run_ids:
        try:
            await service.stop_run(run_id)
        except Exception:  # noqa: BLE001
            pass


async def _register_execution_environment(
    app: Any,
    *,
    environment_id: str,
    workspace_id: str,
    owner_user_id: str | None,
    session: Any | None,
    root: str,
    run_as: str | None = None,
    terminal: TerminalSpec | None = None,
    sources: Optional[List[Dict[str, Any]]] = None,
) -> Any:
    """Register one explicit host or sandbox execution environment.

    Files and Git resolve this identity through ``ExecutionEnvironmentManager``
    even when no agent Run is active. No process-global sandbox lookup or host
    fallback participates in request routing.
    """
    service = getattr(app.state, "agent_service", None)
    if service is None:
        raise RuntimeError("app has no AgentService")

    from omniagents.execution import (
        EnvironmentCapability,
        EnvironmentKind,
        EnvironmentState,
        ExecutionEnvironmentDescriptor,
        TerminalExecutionDefaults,
        WorkspaceResource,
    )
    from omniagents.workspace import HostWorkspace
    from omniagents.workspace.sandbox import SandboxWorkspace

    workspace = (
        HostWorkspace(root=root)
        if session is None
        else SandboxWorkspace(
            session=session,
            root=root,
            default_user=run_as,
        )
    )
    service.workspace_registry.register(
        WorkspaceResource(
            workspace_id,
            owner_user_id=owner_user_id,
            sources=tuple(dict(source) for source in (sources or [])),
        )
    )
    descriptor = ExecutionEnvironmentDescriptor(
        environment_id=environment_id,
        workspace_id=workspace_id,
        kind=(EnvironmentKind.HOST if session is None else EnvironmentKind.DEVBOX),
        state=EnvironmentState.READY,
        generation=1,
        capabilities=frozenset(
            {
                EnvironmentCapability.WORKSPACE_READ,
                EnvironmentCapability.WORKSPACE_WRITE,
                EnvironmentCapability.PROCESS,
                EnvironmentCapability.NETWORK,
                EnvironmentCapability.PTY,
                EnvironmentCapability.HTTP,
                EnvironmentCapability.SERVICES,
                EnvironmentCapability.ENVIRONMENT_LIFECYCLE,
            }
        ),
    )
    sandbox_runtime = None
    if session is not None:
        from omniagents.execution import SandboxExecutionRuntime

        sandbox_runtime = SandboxExecutionRuntime(session=session, run_as=run_as)
    await service.environment_manager.register(
        descriptor,
        workspace,
        sandbox_runtime=sandbox_runtime,
        terminal_defaults=TerminalExecutionDefaults(
            shell=terminal.command if terminal is not None else None,
            cwd=terminal.cwd if terminal is not None else None,
            user=terminal.user if terminal is not None else None,
        ),
    )
    return descriptor


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------


async def teardown_session(
    session: Any,
    started_services: List[Tuple[ServiceSpec, int]],
    *,
    discard_snapshot: bool = False,
    detach: bool = False,
    destroy: bool = False,
    state_path: Optional[Path] = None,
) -> None:
    """Release a session one of three ways.

    * ``detach=True`` — the backend is a durable container session (docker):
      leave the container, its services, and its PTYs running. The next
      serve process reattaches via the state record. Default for docker on
      ordinary serve shutdown. Note: the SDK's ``aclose()`` runs
      ``stop()`` + ``shutdown()`` (which stops the container), so detach
      deliberately calls NOTHING on the session — its process-local
      resources die with this serve process.
    * ``destroy=True`` — terminal shutdown (chat closed/archived, or a
      half-initialized fresh container after a failed bring-up): stop the
      container, remove it, and delete *state_path*.
    * neither — legacy stop for non-durable backends: stop services, persist
      the workspace snapshot (unless ``discard_snapshot``), close.

    Safe to call on a partially-started session, or on ``None`` (the host
    target has no session to tear down).
    """
    if session is None:
        return
    if detach and not destroy:
        return
    # pty_terminate_all is broad but appropriate — every PTY in this session
    # was started by us.
    if started_services:
        try:
            await session.pty_terminate_all()
        except Exception:
            pass
    # If the container is paused, unpause first — session.stop() →
    # persist_snapshot drives ``session.exec`` to write the fingerprint
    # helper output, which hangs forever on a paused container. First-class
    # proxy; SandboxLifecycleUnsupported (backend without unpause) and
    # backend errors are both swallowed — cheap no-op either way.
    try:
        await session.unpause()
    except Exception:
        pass
    # ``session.stop()`` persists the workspace snapshot internally
    # (``_persist_snapshot`` → ``snapshot_lifecycle.persist_snapshot``)
    # for any non-noop snapshot configuration; the SDK does not expose
    # a separate public ``persist_snapshot`` method on the session.
    # Snapshot is best-effort: log any failure so the launcher can
    # surface it, but don't block shutdown.
    if discard_snapshot or destroy:
        # ``persist_snapshot`` early-returns on a NoopSnapshot, so swapping
        # one in is the supported way to skip the persist without forking
        # the SDK's stop path.
        try:
            from agents.sandbox.snapshot import NoopSnapshot

            session.state.snapshot = NoopSnapshot(id=session.state.snapshot.id)
        except Exception as e:
            print(f"[teardown] snapshot discard failed: {e}", file=sys.stderr)
    # No scrub here: every consumer of the tar (``bring_up_session`` on boot,
    # switch, and rollback) scrubs before hydrating, so a teardown-side scrub
    # would just be a second full read+rewrite of the tar on every stop.
    try:
        await session.stop()
    except Exception as e:
        print(f"[teardown] session stop failed: {e}", file=sys.stderr)
    if destroy:
        from omniagents.core.sandbox.lifecycle_session import resolve_backend_session

        remove = getattr(resolve_backend_session(session), "remove_container", None)
        if callable(remove):
            try:
                await remove()
            except Exception as e:  # noqa: BLE001
                print(f"[teardown] container remove failed: {e}", file=sys.stderr)
        if state_path is not None:
            try:
                state_path.unlink(missing_ok=True)
            except OSError:
                pass
    try:
        await session.aclose()
    except Exception:
        pass
