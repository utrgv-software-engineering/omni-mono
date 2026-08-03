"""Serve-session harness: bring an agent's sandbox session up against a
profile, keep it healthy, switch it in place, and tear it down.

This is the generic core of a product's ``<cli> serve`` runtime, lifted
from omni-code's ``serve_cli.py``. The product keeps the CLI surface,
spec loading, FastAPI/uvicorn wiring, and the readiness protocol; the
harness owns everything between "profile resolved" and "live session
attached to the running agent":

* :func:`bring_up_session` — build the sandbox client from a
  :class:`~omniagents.core.sandbox.profiles.ResolvedProfile`,
  create-or-resume the session (3-tier docker resume), scrub + hydrate
  the snapshot, run init commands, start services, resolve their URLs.
* :func:`perform_switch` — swap the running server's sandbox to a new
  profile in place (quiesce, carry, teardown, bring-up, re-wire,
  broadcast, rollback) through its environment lifecycle controller.
* :func:`teardown_session` — stop services, persist the workspace
  snapshot, close the session. Idempotent and ``None``-safe.
Runtime placement is returned to the AgentHost for explicit environment
registration. The harness does not publish filesystem or sandbox handles in
process-global state.

Product knobs, defaulting to the omni-code values they were lifted with:

* ``NOOP_SNAPSHOT_CLIENT_TYPES`` — client types whose lifecycle snapshot
  is a noop (the workspace already lives on durable host storage).
* ``DEFAULT_SERVICE_URL_SUFFIXES`` — per-service-name URL suffixes
  applied when resolving exposed-port endpoints (noVNC's client page for
  a service named ``vnc``).
* ``SwitchContext.profile_resolver`` — the product's layered profile
  resolver (adds its packaged default layer); the framework resolver is
  the fallback.
"""

from __future__ import annotations

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
    resolve_profile,
)

__all__ = [
    "BroughtUpSession",
    "bring_up_session",
    "perform_switch",
    "teardown_session",
    "NOOP_SNAPSHOT_CLIENT_TYPES",
    "DEFAULT_SERVICE_URL_SUFFIXES",
]


# Client types whose lifecycle snapshot is a noop: the workspace IS a
# durable directory (on this host for ``unix_local``, on the remote host
# for ``host_bridge``), so tarring it on stop would be pure overhead —
# and switching AWAY from them must carry the workspace by hand (see
# :func:`_carry_workspace_snapshot`).
NOOP_SNAPSHOT_CLIENT_TYPES: Tuple[str, ...] = ("unix_local", "host_bridge")

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


_DEFAULT_IDLE_THRESHOLD_S = 15 * 60


def _parse_idle_threshold(raw: Optional[Any]) -> float:
    """Read ``idle.pause_after_seconds`` from a resolved profile dict.

    Missing block → 15-minute default. Explicit ``0`` (or negative) disables
    the watcher. Non-numeric values fall back to the default and log a warning;
    we'd rather paper over a YAML typo than fail the launch.
    """
    if not isinstance(raw, dict):
        return float(_DEFAULT_IDLE_THRESHOLD_S)
    value = raw.get("pause_after_seconds")
    if value is None:
        return float(_DEFAULT_IDLE_THRESHOLD_S)
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        print(
            f"[serve] idle.pause_after_seconds is not a number ({value!r}); using default",
            file=sys.stderr,
        )
        return float(_DEFAULT_IDLE_THRESHOLD_S)
    if seconds < 0:
        return 0.0
    return seconds


def _build_snapshot(
    *,
    snapshot_dir: Optional[Path],
    session_id: str,
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
    the unsafe-symlink accumulation that ``_scrub_session_snapshot``
    exists to mop up. ``host_bridge``: the workspace is a durable
    directory on the *remote host* (the user's laptop), reachable only
    via the exec channel. Tarring it back to the serve host's disk would
    both defeat the privacy intent and be pure overhead — same reasoning
    as ``unix_local``'s host directory. *no_snapshot_client_types* is
    the product knob for that set.

    The snapshot id is the caller-supplied (or auto-generated) session
    id, which is the stable identifier for this resumable workspace. The
    next ``client.create()`` with the same session id restores the
    prior workspace state instead of re-running the seed entry.
    """
    if snapshot_dir is None:
        return None
    if client_type in no_snapshot_client_types:
        return None
    from agents.sandbox.snapshot import LocalSnapshot

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    return LocalSnapshot(id=session_id, base_path=snapshot_dir)


async def _create_or_resume_session(
    *,
    client: Any,
    client_type: Optional[str],
    manifest: Any,
    options: Any,
    snapshot: Any,
    session_id: str,
    prior_container_id: Optional[str],
) -> Tuple[Any, Optional[str]]:
    """Create a new session or resume from a prior container, returning the
    session plus a tier tag the launcher uses to decide whether to notify the
    user that runtime state was lost.

    Tier semantics:
      * ``"reused"`` — warm reattach. The container at ``prior_container_id``
        was still alive; processes, env, and apt/pip installs all survive.
      * ``"rehydrated"`` — original container was gone, but the snapshot tar
        existed. Workspace files restored into a brand-new container; running
        processes are gone.
      * ``"fresh"`` — both gone. Manifest-only seed; user lost everything
        but their on-disk source mounts.
      * ``None`` — caller did not request resume (first launch / no prior
        container_id supplied). Treated as the silent baseline.

    Only the docker backend supports the resume path today. For ``unix_local``
    the host filesystem IS the persistence layer and there's nothing to
    reattach to. For other backends we conservatively fall through to
    ``client.create``; future backends can opt into resume.
    """
    if prior_container_id and client_type == "docker":
        # ``DockerSandboxClient.resume(state)`` already handles all three tiers
        # internally: it tries ``get_container(state.container_id)`` and either
        # reattaches or creates a replacement with the same session_id and
        # ``workspace_root_ready=False``. The replacement path triggers
        # ``_start_workspace`` to restore the snapshot tar (rehydrated) or
        # apply the manifest from scratch (fresh).
        import uuid as _uuid

        from agents.sandbox.sandboxes.docker import DockerSandboxSessionState
        from agents.sandbox.snapshot import resolve_snapshot

        # ``snapshot`` may be ``None`` if --snapshot-dir wasn't set; coerce to
        # ``NoopSnapshot`` so ``DockerSandboxSessionState`` validates. The
        # snapshot id is the session_id, matching ``client.create``'s wiring.
        snapshot_instance = resolve_snapshot(snapshot, session_id)
        # ``--session-id`` is any stable caller string (the launcher's
        # residents use readable slugs like ``resident-<id>``); the SDK's
        # state model types it as UUID. Non-UUID ids map through uuid5 so the
        # same string resumes to the same UUID every time, while real UUIDs
        # round-trip unchanged. The snapshot keeps the raw string id above.
        try:
            state_session_uuid = _uuid.UUID(session_id)
        except ValueError:
            state_session_uuid = _uuid.uuid5(
                _uuid.NAMESPACE_URL, f"omni-serve:{session_id}"
            )
        state = DockerSandboxSessionState(
            session_id=state_session_uuid,
            manifest=manifest,
            image=options.image,
            snapshot=snapshot_instance,
            container_id=prior_container_id,
            exposed_ports=options.exposed_ports,
        )
        # Did the underlying tar actually exist? We answer this BEFORE start()
        # because the SDK clears the tar's restorability state once hydration
        # runs (cache invalidation, fingerprint resets). Used to distinguish
        # rehydrate from fresh below.
        try:
            snapshot_restorable = await snapshot_instance.restorable(dependencies=None)
        except Exception:
            snapshot_restorable = False

        session = await client.resume(state)
        # ``resume`` mutates state.container_id to the freshly-minted id when
        # the old container couldn't be found. Comparing identifies the warm
        # reattach without poking SDK privates.
        warm_reattach = str(session.state.container_id) == prior_container_id
        if warm_reattach:
            return session, "reused"
        if snapshot_restorable:
            return session, "rehydrated"
        return session, "fresh"

    session = await client.create(
        manifest=manifest,
        options=options,
        snapshot=snapshot,
    )
    return session, None


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


# Sentinel process_id for a service we adopted from a warm/reused container
# rather than launching ourselves. We have no PTY pid for it; ``teardown_session``
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

    Reuse-aware: when the session is brought up on a warm/reused container
    (resumed from idle pause or re-attached), a service from the previous boot
    may still be running and holding its port. Relaunching it would just bind-
    clash (e.g. code-server exits 1 with ``EADDRINUSE``), so for a service that
    declares a port we first probe whether that port is already listening and,
    if so, adopt the existing instance instead of starting a duplicate. On a
    cold container nothing is listening, so the probe is a cheap no-op and the
    launch proceeds normally.
    """
    started: List[Tuple[ServiceSpec, int]] = []
    for svc in services:
        # Adopt an already-running instance on a reused container.
        if svc.port is not None and await _port_is_listening(session, svc.port):
            print(
                f"[services] {svc.name!r} already listening on port {svc.port}; "
                "adopting existing instance (reused container)",
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
    derived state the serve host and ``perform_switch`` consume afterward."""

    session: Any
    started_services: List[Tuple[ServiceSpec, int]]
    service_urls: Dict[str, Any]
    resume_tier: Optional[str]
    manifest: Any
    client_type: Optional[str]
    container_id: Optional[str]


async def bring_up_session(
    *,
    profile: ResolvedProfile,
    sources_parsed: List[Dict[str, Any]],
    session_id: str,
    snapshot_dir: Optional[Path],
    prior_container_id: Optional[str],
    credentials_parsed: Optional[List[Dict[str, Any]]] = None,
) -> BroughtUpSession:
    """Build the sandbox client from *profile*, create-or-resume the session,
    start it, run init + services, and return its runtime resources. Raises
    ``ValueError`` for a bad profile/source; tears the partial
    session down (re-raising) if init/services fail.

    Shared by first launch (the serve host) and ``perform_switch`` so both
    paths bring sessions up identically.
    """
    client, options, manifest = _build_sandbox(profile, sources=sources_parsed)

    client_type_raw = (profile.raw.get("client") or {}).get("type")
    client_type = client_type_raw if isinstance(client_type_raw, str) else None
    snapshot = _build_snapshot(
        snapshot_dir=snapshot_dir, session_id=session_id, client_type=client_type
    )

    session, resume_tier = await _create_or_resume_session(
        client=client,
        client_type=client_type,
        manifest=manifest,
        options=options,
        snapshot=snapshot,
        session_id=session_id,
        prior_container_id=prior_container_id,
    )

    # Idle-pause threshold from the profile YAML before start() arms the
    # watcher. First-class proxy on the framework's session wrapper; no-ops
    # on backends without idle machinery.
    raw_idle = profile.raw.get("idle")
    if isinstance(raw_idle, dict) and "pause_after_seconds" in raw_idle:
        session.set_idle_threshold(_parse_idle_threshold(raw_idle))

    # Scrub the snapshot tar BEFORE start() hydrates it — this is the ONLY
    # scrub site (teardown deliberately doesn't scrub; see teardown_session).
    # Without it, a persisted tar containing an unsafe symlink (e.g. a
    # committed `.fvm/flutter_sdk -> /Users/...` absolute symlink in a cloned
    # repo) would make `start()` throw WorkspaceArchiveWriteError and the
    # session would never come up. Best-effort + idempotent.
    _scrub_session_snapshot(session)

    await session.start()
    started_services: List[Tuple[ServiceSpec, int]] = []
    try:
        # Configure git + CLI auth before init/services and before the agent
        # runs, so private clones already done during create() are joined by
        # working `gh` / `az devops` for the agent's own commands. Written as the
        # profile's run_as identity so creds land in the HOME the agent uses.
        await _configure_credentials(
            session,
            credentials_parsed or [],
            run_as_user=_run_as_exec_user(profile.raw),
        )
        await _run_init(session, profile.init)
        started_services = await _start_services(session, profile.services)
        service_urls = await _resolve_service_urls(session, started_services)
    except Exception:
        await teardown_session(session, started_services)
        raise

    return BroughtUpSession(
        session=session,
        started_services=started_services,
        service_urls=service_urls,
        resume_tier=resume_tier,
        manifest=manifest,
        client_type=client_type,
        container_id=getattr(session.state, "container_id", None),
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


def _changed_payload(ctx: Any) -> Dict[str, Any]:
    return {
        "profile": ctx.current_profile_name,
        "backend": ctx.backend,
        "container_id": ctx.container_id,
        "services": ctx.service_urls,
    }


async def _broadcast_sandbox_event(
    service: Any, method: str, payload: Dict[str, Any], session: Any = None
) -> None:
    """Best-effort push of a ``ui.sandbox.*`` event to the session's channels.
    Mirrors ``omniagents.builtin.server_functions.bash_jobs._broadcast_snapshot``."""
    if service is None:
        return
    try:
        await service.request_client_function(
            method, payload, session=session, expect_result=False
        )
    except Exception as e:  # noqa: BLE001
        print(f"[sandbox-switch] broadcast {method} failed: {e}", file=sys.stderr)


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


def _resolve_switch_profile(ctx: Any, explicit_path: Optional[Path]) -> ResolvedProfile:
    """Resolve a profile for a switch/rollback through the product's layered
    resolver when the switch context carries one
    (``SwitchContext.profile_resolver``), else the framework resolver."""
    resolver = getattr(ctx, "profile_resolver", None) or resolve_profile
    return resolver(
        workspace_dir=ctx.workspace_dir,
        project_id=ctx.project_id,
        explicit_path=explicit_path,
    )


async def _attempt_rollback(ctx: Any, prev_profile_path: Optional[str]) -> bool:
    """Bring the previous profile back up from the (intact) snapshot and re-wire
    it. Returns True on success. The workspace tar the failed switch's teardown
    persisted is keyed by session_id, so the rollback rehydrates the same files."""
    if not prev_profile_path:
        return False
    try:
        prev = _resolve_switch_profile(ctx, Path(prev_profile_path))
        brought = await bring_up_session(
            profile=prev,
            sources_parsed=ctx.sources_parsed,
            credentials_parsed=ctx.credentials_parsed,
            session_id=ctx.session_id,
            snapshot_dir=ctx.snapshot_dir,
            prior_container_id=None,
        )
    except Exception:  # noqa: BLE001
        return False
    await _replace_execution_backend(
        ctx,
        brought.session,
        run_as=_run_as_exec_user(prev.raw),
        terminal=prev.terminal,
    )
    ctx.backend = brought.client_type
    ctx.container_id = brought.container_id
    ctx.service_urls = brought.service_urls
    ctx.started_services = brought.started_services
    return True


async def _carry_workspace_snapshot(
    *, session: Any, session_id: str, snapshot_dir: Any
) -> None:
    """Tar *session*'s workspace into ``<snapshot_dir>/<session_id>.tar`` so the
    next ``bring_up_session`` rehydrates it into the new backend.

    Needed only when switching AWAY from a backend whose lifecycle snapshot is a
    noop — ``host_bridge`` (laptop sandbox) and ``unix_local`` (see
    :func:`_build_snapshot`). Those write no tar on ``session.stop()``, so without
    this the new backend seeds fresh and silently drops all uncommitted work.
    Real-snapshot backends (docker/aci) already persist on teardown, so callers
    skip this for them to avoid tarring the workspace twice. The tar format
    (``persist_workspace`` → ``hydrate_workspace``) is shared across backends, so
    a host_bridge-produced tar restores cleanly under aci.
    """
    from agents.sandbox.snapshot import LocalSnapshot

    data = await session.persist_workspace()
    await LocalSnapshot(id=session_id, base_path=snapshot_dir).persist(data)


async def _replace_execution_backend(
    ctx: Any,
    sandbox_session: Any,
    *,
    run_as: str | None,
    terminal: TerminalSpec,
) -> None:
    """Publish a switched sandbox as the next generation of one environment."""

    from omniagents.execution import (
        SandboxExecutionRuntime,
        TerminalExecutionDefaults,
    )
    from omniagents.workspace.sandbox import SandboxWorkspace

    service = getattr(ctx.app.state, "agent_service", None)
    if service is None:
        raise RuntimeError("switch context app has no AgentService")
    root = getattr(
        getattr(getattr(sandbox_session, "state", None), "manifest", None),
        "root",
        None,
    )
    if not isinstance(root, str) or not root:
        raise RuntimeError("switched sandbox has no manifest root")
    workspace = SandboxWorkspace(
        session=sandbox_session,
        root=root,
        default_user=run_as,
    )
    await service.environment_manager.replace(
        ctx.environment_id,
        workspace,
        sandbox_runtime=SandboxExecutionRuntime(
            session=sandbox_session,
            run_as=run_as,
        ),
        terminal_defaults=TerminalExecutionDefaults(
            shell=terminal.command,
            cwd=terminal.cwd,
            user=terminal.user,
        ),
    )


async def perform_switch(
    profile_path: str,
    *,
    session: Any = None,
    switch_context: Any,
) -> Dict[str, Any]:
    """Switch the running server's sandbox to the profile at *profile_path*, in
    place — no process restart. Snapshots the current workspace, brings up the
    new backend from the same snapshot (rehydrate), re-attaches it to the live
    agent spec, and broadcasts ``ui.sandbox.changed``. Rolls back to the previous
    profile if bring-up fails.

    Operator command, invoked by the ``sandbox.switch`` server function (the
    launcher) — never the LLM. *session* is the calling conversation session,
    used to route the ``ui.sandbox.*`` broadcasts. Returns
    ``{ok, profile, backend, container_id, services, reason?}``.

    The environment lifecycle controller supplies its own SwitchContext;
    callers cannot select a process-global current sandbox.
    """
    ctx = switch_context

    try:
        from omniagents.core.agents.service import current_service

        service = current_service()
    except Exception:
        service = None

    async with ctx.lock:
        try:
            target = _resolve_switch_profile(ctx, Path(profile_path))
        except (FileNotFoundError, ValueError) as e:
            return {"ok": False, "reason": f"profile: {e}"}

        if ctx.current_profile_path and Path(profile_path) == Path(
            ctx.current_profile_path
        ):
            return {"ok": True, **_changed_payload(ctx)}

        target_name = _profile_display_name(profile_path, target)
        prev_profile_path = ctx.current_profile_path

        environment_manager = ctx.app.state.agent_service.environment_manager
        old_lease = await environment_manager.acquire(ctx.environment_id)
        old_runtime = old_lease.sandbox_runtime
        old_session = old_runtime.session if old_runtime is not None else None

        # Quiesce before tearing the old sandbox down.
        await _cancel_active_runs(service)

        ctx.switching = True
        ctx.last_error = None
        ctx.ready_event.clear()
        from omniagents.execution import EnvironmentState

        await environment_manager.transition(
            ctx.environment_id, EnvironmentState.REPLACING
        )
        await _broadcast_sandbox_event(
            service, "ui.sandbox.switching", {"profile": target_name}, session=session
        )

        # Carry the workspace across the switch when the OLD backend uses a noop
        # lifecycle snapshot (host_bridge laptop sandbox / unix_local):
        # teardown_session → session.stop() persists nothing for those, so tar
        # the workspace into the snapshot dir NOW; bring_up_session below then
        # rehydrates it into the new backend. Without this, switching off a
        # laptop sandbox silently drops all uncommitted work. Best-effort: a
        # flaky carry must not block the switch (the new backend just seeds
        # fresh, same as today).
        if (
            old_session is not None
            and ctx.snapshot_dir is not None
            and ctx.backend in NOOP_SNAPSHOT_CLIENT_TYPES
        ):
            try:
                await _carry_workspace_snapshot(
                    session=old_session,
                    session_id=ctx.session_id,
                    snapshot_dir=ctx.snapshot_dir,
                )
            except Exception as e:  # noqa: BLE001
                print(
                    json.dumps(
                        {"warning": f"workspace carry failed during switch: {e}"}
                    ),
                    file=sys.stderr,
                )
        try:
            if old_session is not None:
                await teardown_session(old_session, ctx.started_services)
            brought = await bring_up_session(
                profile=target,
                sources_parsed=ctx.sources_parsed,
                credentials_parsed=ctx.credentials_parsed,
                session_id=ctx.session_id,
                snapshot_dir=ctx.snapshot_dir,
                prior_container_id=None,
            )
        except Exception as e:  # noqa: BLE001
            ctx.last_error = str(e)
            rolled = await _attempt_rollback(ctx, prev_profile_path)
            ctx.switching = False
            ctx.ready_event.set()
            if rolled:
                await _broadcast_sandbox_event(
                    service,
                    "ui.sandbox.changed",
                    _changed_payload(ctx),
                    session=session,
                )
                # Old sandbox restored and live — the caller should NOT relaunch.
                return {
                    "ok": False,
                    "recovered": "rolled_back",
                    "reason": f"switch failed, rolled back: {e}",
                }
            await environment_manager.transition(
                ctx.environment_id, EnvironmentState.FAILED
            )
            await _broadcast_sandbox_event(
                service, "ui.sandbox.lost", {"reason": str(e)}, session=session
            )
            # Sandbox is gone — the caller must cold-relaunch to recover.
            return {
                "ok": False,
                "recovered": "lost",
                "reason": f"switch failed and rollback failed: {e}",
            }

        # Publish the new SDK session as the next environment generation. The
        # shared AgentSpec remains declarative and untouched.
        await _replace_execution_backend(
            ctx,
            brought.session,
            run_as=_run_as_exec_user(target.raw),
            terminal=target.terminal,
        )

        ctx.current_profile_path = profile_path
        ctx.current_profile_name = target_name
        ctx.backend = brought.client_type
        ctx.container_id = brought.container_id
        ctx.service_urls = brought.service_urls
        ctx.started_services = brought.started_services
        ctx.switching = False
        ctx.ready_event.set()

        await _broadcast_sandbox_event(
            service, "ui.sandbox.changed", _changed_payload(ctx), session=session
        )
        return {"ok": True, **_changed_payload(ctx)}


# ---------------------------------------------------------------------------
# Terminal managers
# ---------------------------------------------------------------------------


class ServeEnvironmentLifecycleController:
    """Environment-scoped adapter over the legacy serve orchestration.

    The instance is registered on one ExecutionEnvironment. Handlers receive
    it through an EnvironmentLease, so they cannot accidentally operate on a
    different process-global sandbox.
    """

    def __init__(self, switch_context: Any) -> None:
        self._switch_context = switch_context

    def _validate(self, lease: Any) -> None:
        if lease.environment_id != self._switch_context.environment_id:
            raise RuntimeError("lifecycle controller/environment mismatch")

    def state(self, lease: Any) -> Dict[str, Any]:
        """Return observable state for this leased environment only."""
        self._validate(lease)
        ctx = self._switch_context
        return {
            "available": True,
            "ready": ctx.ready_event.is_set() and not ctx.switching,
            "switching": ctx.switching,
            "profile": ctx.current_profile_name,
            "backend": ctx.backend,
            "container_id": ctx.container_id,
            "last_error": ctx.last_error,
        }

    async def wait_until_ready(
        self, lease: Any, timeout_seconds: float
    ) -> Dict[str, Any]:
        """Wait for this environment's current replacement to finish."""
        import asyncio

        self._validate(lease)
        ctx = self._switch_context
        try:
            await asyncio.wait_for(ctx.ready_event.wait(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            return {
                "ready": False,
                "profile": ctx.current_profile_name,
                "reason": "timeout",
            }
        return {
            "ready": not ctx.switching,
            "profile": ctx.current_profile_name,
            "switching": ctx.switching,
            "last_error": ctx.last_error,
        }

    @staticmethod
    def _backend(lease: Any) -> Any:
        from omniagents.core.sandbox.lifecycle_session import (
            resolve_backend_session,
        )
        from omniagents.workspace.sandbox import SandboxWorkspace

        workspace = lease.workspace
        if not isinstance(workspace, SandboxWorkspace):
            return None
        return resolve_backend_session(workspace.session)

    async def pause(self, lease: Any) -> Dict[str, Any]:
        inner = self._backend(lease)
        if inner is None or not hasattr(inner, "pause"):
            return {
                "ok": False,
                "supported": False,
                "reason": "environment backend does not implement pause",
            }
        try:
            await inner.pause()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "supported": True, "reason": str(exc)}
        paused = True
        if hasattr(inner, "paused"):
            try:
                paused = bool(await inner.paused())
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True, "supported": True, "paused": paused}

    async def unpause(self, lease: Any) -> Dict[str, Any]:
        inner = self._backend(lease)
        if inner is None or not hasattr(inner, "unpause"):
            return {
                "ok": False,
                "supported": False,
                "reason": "environment backend does not implement unpause",
            }
        try:
            await inner.unpause()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "supported": True, "reason": str(exc)}
        paused = False
        if hasattr(inner, "paused"):
            try:
                paused = bool(await inner.paused())
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True, "supported": True, "paused": paused}

    async def notify_activity(self, lease: Any) -> Dict[str, Any]:
        inner = self._backend(lease)
        if inner is None or not hasattr(inner, "notify_activity"):
            return {"ok": False}
        try:
            await inner.notify_activity()
        except Exception:  # noqa: BLE001
            return {"ok": False}
        return {"ok": True}

    async def discard_snapshot(self, lease: Any) -> Dict[str, Any]:  # noqa: ARG002
        self._switch_context.discard_snapshot_on_exit = True
        return {"ok": True}

    async def switch(self, lease: Any, profile: str) -> Dict[str, Any]:
        self._validate(lease)
        return await perform_switch(profile, switch_context=self._switch_context)

    async def close(self, lease: Any) -> None:
        """Release this environment's live session and started services."""

        self._validate(lease)
        ctx = self._switch_context
        runtime = lease.sandbox_runtime
        session = runtime.session if runtime is not None else None
        await teardown_session(
            session,
            ctx.started_services,
            discard_snapshot=ctx.discard_snapshot_on_exit,
        )
        ctx.started_services = []
        ctx.ready_event.clear()


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
) -> None:
    """Stop services + persist the workspace snapshot + close the session.

    Safe to call on a partially-started session, or on ``None`` (the host
    target has no session to tear down).

    ``discard_snapshot=True`` skips the workspace snapshot persist entirely
    (fingerprint pass + full tar export). Only for terminal shutdowns — the
    launcher deletes the tar right after this process exits
    (``sandbox.discard_snapshot``). Never set it on a switch: the switch
    rehydrates the new backend from the snapshot this persist writes.
    """
    if session is None:
        return
    # pty_terminate_all is broad but appropriate — every PTY in this session
    # was started by us.
    if started_services:
        try:
            await session.pty_terminate_all()
        except Exception:
            pass
    # If the container was paused for idle, unpause first — session.stop()
    # → persist_snapshot drives ``session.exec`` to write the fingerprint
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
    if discard_snapshot:
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
    try:
        await session.aclose()
    except Exception:
        pass
