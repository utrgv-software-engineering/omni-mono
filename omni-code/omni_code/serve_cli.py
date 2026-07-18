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
import sys
import tarfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from omni_code.sandbox_profile import (
    ResolvedProfile,
    ServiceSpec,
    TerminalSpec,
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
    # The manifest lives inside the package — same path for editable and
    # wheel installs.
    pkg_project = Path(__file__).resolve().parent / "project.yml"
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
    exists to mop up.

    The snapshot id is the caller-supplied (or auto-generated) session
    id, which is the stable identifier for this resumable workspace. The
    next ``client.create()`` with the same session id restores the
    prior workspace state instead of re-running the seed entry.
    """
    if snapshot_dir is None:
        return None
    if client_type in ("unix_local", "host_bridge"):
        # host_bridge: the workspace is a durable directory on the *remote host*
        # (the user's laptop), reachable only via the exec channel. Tarring it
        # back to the cloud omni-serve disk would both defeat the privacy intent
        # and be pure overhead — same reasoning as unix_local's host directory.
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
        state = DockerSandboxSessionState(
            session_id=_uuid.UUID(session_id),
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


def _load_spec(project_path: Path, agent_key: Optional[str]) -> Any:
    from omniagents.core.project.runtime import (
        build_spec_from_project,
        load_project,
    )
    from omni_code.agent_workspace_state import set_project_context

    proj = load_project(str(project_path))
    # Publish the resolved ProjectContext so worker spec loading
    # (omni_code/workers_runtime._load_and_configure_spec) can route the worker
    # through build_spec_from_project with the same capability/tool/
    # guardrail registries. Without this, the worker would load its
    # agent.yml in isolation with capability_registry=None and silently
    # drop every entry in its `capabilities:` list, leaving it with zero
    # tools attached.
    set_project_context(proj)
    return build_spec_from_project(proj, agent_key=agent_key)


def _attach_session_to_spec(
    spec: Any, session: Any, profile_raw: Dict[str, Any]
) -> None:
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
    *name* (``tokenEnv``), read host-side here from the omni serve process env
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
        url = f"http://{endpoint.host}:{endpoint.port}"
        if svc.name == "vnc":
            url = f"{url}/vnc.html?autoconnect=true&resize=scale"
        out[svc.name] = url
    return out


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


@dataclass
class BroughtUpSession:
    """Result of bringing a sandbox session up: the live session plus the
    derived state ``_serve`` and ``perform_switch`` consume afterward."""

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
    start it, run init + services, and publish the manifest/sources/live-session
    slots. Raises ``ValueError`` for a bad profile/source; tears the partial
    session down (re-raising) if init/services fail.

    Shared by first launch (``_serve``) and ``perform_switch`` so both paths
    bring sessions up identically.
    """
    from omni_code.agent_workspace_state import (
        set_manifest_root,
        set_sandbox_session,
        set_sources,
    )

    client, options, manifest = _build_sandbox(profile, sources=sources_parsed)
    set_manifest_root(str(manifest.root) if manifest.root is not None else None)
    set_sources(sources_parsed)

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

    # Idle-pause threshold from the profile YAML before start() arms the watcher.
    inner = getattr(session, "_inner", session)
    if hasattr(inner, "set_idle_threshold"):
        raw_idle = profile.raw.get("idle")
        if isinstance(raw_idle, dict) and "pause_after_seconds" in raw_idle:
            inner.set_idle_threshold(_parse_idle_threshold(raw_idle))

    # Scrub the snapshot tar BEFORE start() hydrates it — this is the ONLY
    # scrub site (teardown deliberately doesn't scrub; see teardown_session).
    # Without it, a persisted tar containing an unsafe symlink (e.g. a
    # committed `.fvm/flutter_sdk -> /Users/...` absolute symlink in a cloned
    # repo) would make `start()` throw WorkspaceArchiveWriteError and the
    # session would never come up. Best-effort + idempotent.
    _scrub_session_snapshot(session)

    await session.start()
    # Publish so ``sandbox.*`` server functions can reach the inner backend.
    set_sandbox_session(session)

    started_services: List[Tuple[ServiceSpec, int]] = []
    try:
        # Configure git + CLI auth before init/services and before the agent
        # runs, so private clones already done during create() are joined by
        # working `gh` / `az devops` for the agent's own commands. Written as the
        # profile's run_as identity so creds land in the HOME the agent uses.
        await _configure_credentials(
            session, credentials_parsed or [], run_as_user=_run_as_exec_user(profile.raw)
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


async def _attempt_rollback(ctx: Any, prev_profile_path: Optional[str]) -> bool:
    """Bring the previous profile back up from the (intact) snapshot and re-wire
    it. Returns True on success. The workspace tar the failed switch's teardown
    persisted is keyed by session_id, so the rollback rehydrates the same files."""
    from omni_code.agent_workspace_state import (
        set_orchestrator_spec,
        set_sandbox_session,
    )

    if not prev_profile_path:
        return False
    try:
        prev = resolve_profile(
            workspace_dir=ctx.workspace_dir,
            project_id=ctx.project_id,
            explicit_path=Path(prev_profile_path),
        )
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
    set_sandbox_session(brought.session)
    _attach_session_to_spec(ctx.spec, brought.session, prev.raw)
    set_orchestrator_spec(ctx.spec)
    _install_session_terminal_manager(ctx.app, brought.session, prev.terminal)
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


async def perform_switch(profile_path: str, *, session: Any = None) -> Dict[str, Any]:
    """Switch the running server's sandbox to the profile at *profile_path*, in
    place — no process restart. Snapshots the current workspace, brings up the
    new backend from the same snapshot (rehydrate), re-attaches it to the live
    agent spec, and broadcasts ``ui.sandbox.changed``. Rolls back to the previous
    profile if bring-up fails.

    Operator command, invoked by the ``sandbox.switch`` server function (the
    launcher) — never the LLM. *session* is the calling conversation session,
    used to route the ``ui.sandbox.*`` broadcasts. Returns
    ``{ok, profile, backend, container_id, services, reason?}``.
    """
    from omni_code.agent_workspace_state import (
        get_sandbox_session,
        get_switch_context,
        set_orchestrator_spec,
        set_sandbox_session,
    )

    ctx = get_switch_context()
    if ctx is None:
        return {
            "ok": False,
            "reason": "no live sandbox server (not running under omni serve)",
        }

    try:
        from omniagents.core.agents.service import current_service

        service = current_service()
    except Exception:
        service = None

    async with ctx.lock:
        try:
            target = resolve_profile(
                workspace_dir=ctx.workspace_dir,
                project_id=ctx.project_id,
                explicit_path=Path(profile_path),
            )
        except (FileNotFoundError, ValueError) as e:
            return {"ok": False, "reason": f"profile: {e}"}

        if ctx.current_profile_path and Path(profile_path) == Path(
            ctx.current_profile_path
        ):
            return {"ok": True, **_changed_payload(ctx)}

        target_name = _profile_display_name(profile_path, target)
        prev_profile_path = ctx.current_profile_path

        # Quiesce before tearing the old sandbox down.
        await _cancel_active_runs(service)

        ctx.switching = True
        ctx.last_error = None
        ctx.ready_event.clear()
        await _broadcast_sandbox_event(
            service, "ui.sandbox.switching", {"profile": target_name}, session=session
        )

        old_session = get_sandbox_session()
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
            and ctx.backend in ("unix_local", "host_bridge")
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
            await _broadcast_sandbox_event(
                service, "ui.sandbox.lost", {"reason": str(e)}, session=session
            )
            # Sandbox is gone — the caller must cold-relaunch to recover.
            return {
                "ok": False,
                "recovered": "lost",
                "reason": f"switch failed and rollback failed: {e}",
            }

        # Re-wire the new session into the live agent.
        set_sandbox_session(brought.session)
        _attach_session_to_spec(ctx.spec, brought.session, target.raw)
        set_orchestrator_spec(ctx.spec)
        _install_session_terminal_manager(ctx.app, brought.session, target.terminal)

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


def _register_codex_provider() -> None:
    """Register the openai-oauth model provider builder. Never blocks serve."""
    try:
        from omni_code.codex_auth import register_codex_provider

        register_codex_provider()
    except Exception as e:
        print(
            json.dumps({"warning": f"codex provider registration skipped: {e}"}),
            file=sys.stderr,
        )


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
        sources_parsed: List[Dict[str, Any]] = []
        for raw in args.sources:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"--source value is not valid JSON: {raw!r}") from e
            if not isinstance(parsed, dict):
                raise ValueError(f"--source must be a JSON object, got {parsed!r}")
            sources_parsed.append(parsed)
    except ValueError as e:
        print(json.dumps({"error": f"source: {e}"}), file=sys.stderr)
        return 1

    # Parse the repeated --credential JSON descriptors (boot-time git/CLI auth).
    try:
        credentials_parsed: List[Dict[str, Any]] = []
        for raw in args.credentials:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"--credential value is not valid JSON: {raw!r}") from e
            if not isinstance(parsed, dict):
                raise ValueError(f"--credential must be a JSON object, got {parsed!r}")
            credentials_parsed.append(parsed)
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
    # Agent against the host filesystem; bind a HostWorkspace as the
    # process-wide default so every run (including workers, which copy the
    # context) routes through it. ``confine`` gates the workspace-root
    # boundary (false = true no-sandbox).
    #
    # Sandbox targets: ``resume_tier`` is "reused"/"rehydrated"/"fresh"/None;
    # ``bring_up_session`` also publishes manifest-root, sources, and the
    # live-session slots, and tears the partial session down if init fails.
    is_host = _is_host_profile(profile.raw)
    if is_host:
        from omniagents.workspace import HostWorkspace, set_default_workspace

        from omni_code.agent_workspace_state import set_manifest_root, set_sources

        session = None
        resume_tier = None
        started_services = []
        service_urls: Dict[str, Any] = {}
        client_type = "host"
        # The host target's workspace root IS the host dir. Publish it as the
        # manifest root so the authoritative workspace_root override (passed to
        # build_app below) reports it uniformly with the sandbox path.
        set_manifest_root(args.workspace)
        # Publish the sources too (bring_up_session does this for sandboxes) so
        # fs_get_sources works under host — the launcher reads it to show the
        # local workspace folder in its pill.
        set_sources(sources_parsed)
        set_default_workspace(
            HostWorkspace(
                root=args.workspace,
                confine=bool(profile.raw.get("confine", True)),
            )
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
    from omni_code.agent_workspace_state import set_orchestrator_spec

    set_orchestrator_spec(spec)

    # --- FastAPI app + uvicorn ---
    from omniagents.backends.server.app import build_app
    import uvicorn

    from omniagents.workspace import clamp_within

    from omni_code.agent_workspace_state import get_manifest_root, get_sandbox_session

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
    # twice without fresh activity in between). The idle-watcher machinery
    # is the framework's; the recap action is omni-code's. Stashed on
    # app.state for teardown + future inspection.
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
    base_url = f"http://{args.host}:{port}"
    ws_url = f"ws://{args.host}:{port}/ws"
    if args.auth_token:
        ws_url = f"{ws_url}?token={args.auth_token}"
    # Pull container_id from session.state when present (docker backends
    # expose it; unix_local / e2b / etc. don't). The launcher uses this
    # for ``docker exec``-based operations like PR diff extraction.
    container_id = (
        None if session is None else getattr(session.state, "container_id", None)
    )
    manifest_root = (
        args.workspace
        if session is None
        else getattr(session.state.manifest, "root", None)
    )
    payload = {
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
        print(f"Omni Code: {base_url}  (profile backend={backend})", flush=True)

    # --- Publish the switch context ---
    # Lets the ``sandbox.switch`` server function rebuild + re-attach the
    # sandbox in place (no process restart). ``ready_event`` starts set: the
    # sandbox is up. Cleared in the teardown ``finally`` below.
    from omni_code.agent_workspace_state import SwitchContext, set_switch_context

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
        current_profile_path=str(args.profile) if args.profile else None,
        current_profile_name=_profile_display_name(args.profile, profile),
        backend=client_type,
        container_id=container_id,
        service_urls=service_urls,
        started_services=started_services,
    )
    switch_ctx.ready_event.set()
    set_switch_context(switch_ctx)

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
        from omni_code.agent_workspace_state import (
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
        # defensive unpause + snapshot persist.
        inner_for_idle_stop = getattr(live_session, "_inner", live_session)
        if hasattr(inner_for_idle_stop, "stop_idle_watcher"):
            try:
                await inner_for_idle_stop.stop_idle_watcher()
            except Exception:
                pass
        from omni_code.agent_workspace_state import get_discard_snapshot_on_exit

        await teardown_session(
            live_session,
            live_services,
            discard_snapshot=get_discard_snapshot_on_exit(),
        )
        # Clear the published process-global slots on exit (teardown_session
        # deliberately leaves the handle alone so switches can replace it).
        set_sandbox_session(None)
        set_switch_context(None)

    return 0


def _is_host_profile(profile_raw: Dict[str, Any]) -> bool:
    """True when the profile selects the ``host`` execution target — no
    sandbox session: plain Agent on a HostWorkspace, host PTY terminal."""
    return ((profile_raw.get("client") or {}).get("type")) == "host"


class _UnsupportedPtyBackend:
    """A ``PtyBackend`` that refuses to start.

    Installed for sandbox backends that can't stream a PTY (e.g. ACI). It makes
    ``terminal.create`` fail loudly with a clear message instead of silently
    falling back to a host shell, which would run outside the sandbox.
    """

    def __init__(self, backend_label: str) -> None:
        self._label = backend_label

    async def start(self, **_: Any) -> Any:
        from omniagents.rpc.terminal_backends import PtyOpenError

        raise PtyOpenError(
            f"Interactive terminals are not supported on the {self._label} "
            "sandbox backend."
        )

    async def write(self, handle: Any, data: bytes) -> None:
        return None

    async def resize(self, handle: Any, cols: int, rows: int) -> None:
        return None

    def stream(self, handle: Any) -> AsyncIterator[bytes]:
        async def _empty() -> AsyncIterator[bytes]:
            return
            yield b""  # pragma: no cover - never reached

        return _empty()

    async def close(self, handle: Any) -> None:
        return None

    async def exit_code(self, handle: Any) -> Optional[int]:
        return None


def _install_session_terminal_manager(
    app: Any, session: Any, terminal: TerminalSpec
) -> None:
    """Attach a session-backed :class:`TerminalManager` to the AgentService.

    Explicit binding, parallel to how
    :class:`omniagents.workspace.sandbox.SandboxWorkspace` binds a
    sandbox session to the agent's filesystem/shell surface. The
    profile's ``terminal:`` block is authoritative for the shell, cwd,
    and user; ``terminal.create`` callers may override per-call. The
    legacy ``session.context['workspace_root']`` (a *host* path) is
    bypassed entirely for this manager — see the explicit-binding
    branch in :func:`server_functions.terminal.terminal_create`.

    Silent no-op when the session backend can't stream PTY output
    (e.g. ``e2b``/``modal`` until those backends grow
    :class:`omniagents.core.sandbox.pty_stream.PtyStreamingSession`
    support). ``terminal.create`` then falls through to the standalone
    host-pty path.
    """
    try:
        from omniagents.core.sandbox.pty_stream import coerce_pty_streaming
        from omniagents.rpc.terminal import TerminalManager
        from omniagents.rpc.terminal_backends import SessionPtyBackend
    except Exception:
        return

    service = getattr(app.state, "agent_service", None)
    if service is None:
        return

    inner = coerce_pty_streaming(session)
    if inner is not None:
        backend: Any = SessionPtyBackend(inner)
    else:
        # Sandbox backend can't stream a PTY. Install a backend
        # that errors loudly on create rather than letting ``terminal.create``
        # fall back to a HOST shell — that would break the isolation model by
        # handing the user a shell on the omni serve host instead of inside
        # the sandbox.
        label = type(getattr(session, "_inner", session)).__name__
        backend = _UnsupportedPtyBackend(label)

    service.terminal_manager = TerminalManager(
        backend=backend,
        default_shell=terminal.command,
        default_cwd=terminal.cwd,
        default_user=terminal.user,
    )


def _install_host_terminal_manager(
    app: Any, terminal: TerminalSpec, *, default_cwd: Optional[Path | str]
) -> None:
    """Attach a host-PTY TerminalManager for the ``host`` execution target.

    The shell runs as a child of the omni serve process (HostPtyBackend) —
    intended here, since the host target has no sandbox to run inside. On
    platforms without a host PTY (Windows), ``HostPtyBackend.start`` raises
    a clear error at create time.
    """
    try:
        from omniagents.rpc.terminal import TerminalManager
        from omniagents.rpc.terminal_backends import HostPtyBackend
    except Exception:
        return

    service = getattr(app.state, "agent_service", None)
    if service is None:
        return

    resolved_cwd = terminal.cwd or (str(default_cwd) if default_cwd is not None else None)

    service.terminal_manager = TerminalManager(
        backend=HostPtyBackend(),
        default_shell=terminal.command,
        default_cwd=resolved_cwd,
        default_user=terminal.user,
    )


async def teardown_session(
    session: Any,
    started_services: List[Tuple[ServiceSpec, int]],
    *,
    discard_snapshot: bool = False,
) -> None:
    """Stop services + persist the workspace snapshot + close the session.

    Does NOT clear the published ``set_sandbox_session`` handle — the caller
    owns that: ``_serve`` nulls it on process exit; ``perform_switch`` replaces
    it with the new session. Safe to call on a partially-started session, or
    on ``None`` (the host target has no session to tear down).

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
    # helper output, which hangs forever on a paused container. Cheap
    # no-op when the session backend doesn't expose unpause.
    inner = getattr(session, "_inner", session)
    if hasattr(inner, "unpause"):
        try:
            await inner.unpause()
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
