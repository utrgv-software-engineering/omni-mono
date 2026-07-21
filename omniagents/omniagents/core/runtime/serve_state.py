"""Process-global serve-session state.

The serve host (a product's ``<cli> serve``) brings up one agent server
per process; this module is where it publishes the session's wiring so
tools, server functions, and prompt builders can see it without weaving
references through every call site. Everything here is process-global by
design — one serve process, one session.

The serve host populates these slots:

* ``manifest_root`` — the path the agent's tools actually see (e.g.
  ``/workspace`` inside a docker session). Read by
  ``fs_get_workspace_root`` and workspace-root clamping.
* ``sources`` — the parsed launcher ``--source`` descriptors that seeded
  the workspace. Each entry has ``kind`` (``local``/``local-git``/
  ``git-remote``), ``mountName``, and either ``path`` (host dir for local
  kinds) or ``repoUrl`` (+ optional ``ref``) for git-remote. Read by
  ``fs_get_sources``, skill-root resolution, and context factories so
  multi-source projects can advertise every directory in play — important
  under host-mode (``unix_local``) where sources are NOT relocated onto
  the primary workspace and would otherwise be invisible to the agent.
* ``project_context`` — the resolved ``ProjectContext`` from
  ``load_project``. Read by worker spec loaders so a worker builds its
  spec through the same code path as the orchestrator
  (``build_spec_from_project``) and inherits the full capability/tool/
  guardrail registries instead of loading its YAML in isolation.
* ``orchestrator_spec`` — the running orchestrator's ``AgentSpec`` after
  the live sandbox session has been attached. Read by worker spec
  loaders to forward ``sandbox_use_sandbox_agent``, ``sandbox_run_as``
  and ``sandbox_run_config`` so worker tools route through the same
  sandbox.
* ``sandbox_session`` — the live ``SandboxSession`` wrapper, for server
  functions that drive container-level lifecycle (pause/unpause) or
  browse the container filesystem.
* ``discard_snapshot_on_exit`` — terminal-shutdown flag: teardown should
  skip the workspace snapshot persist because the launcher deletes the
  snapshot right after the process exits.
* ``switch_context`` — everything a profile switch needs to rebuild the
  sandbox session in place (:class:`SwitchContext`).

All slots default to ``None``/empty so non-serve entry points (tests,
direct invocation, evals) see the absence and fall back to their own
discovery. The serve host clears the live-object slots on teardown so
stale state never outlives the server.

Lifted from omni-code's ``omni_code/agent_workspace_state.py``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Union

__all__ = [
    "SourceDescriptor",
    "SwitchContext",
    "set_manifest_root",
    "get_manifest_root",
    "set_sources",
    "get_sources",
    "get_source_descriptors",
    "set_project_context",
    "get_project_context",
    "set_orchestrator_spec",
    "get_orchestrator_spec",
    "set_sandbox_session",
    "get_sandbox_session",
    "set_discard_snapshot_on_exit",
    "get_discard_snapshot_on_exit",
    "set_switch_context",
    "get_switch_context",
    "reset",
]


# --------------------------------------------------------------------------
# Source descriptors — the launcher ``--source`` dict protocol, typed.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceDescriptor:
    """One workspace source, as declared to the launcher via ``--source``.

    The wire protocol is a JSON dict (``kind``/``mountName`` plus
    ``path`` for local kinds or ``repoUrl``/``ref`` for git-remote);
    :meth:`from_dict` / :meth:`to_dict` round-trip it. The published
    state keeps the raw dicts (so unknown keys survive for UI callers);
    typed consumers read :func:`get_source_descriptors`.
    """

    kind: str = ""
    mount_name: str = ""
    path: Optional[str] = None
    repo_url: Optional[str] = None
    ref: Optional[str] = None

    LOCAL_KINDS = ("local", "local-git")
    GIT_KINDS = ("local-git", "git-remote")

    @property
    def is_local(self) -> bool:
        """True for kinds whose ``path`` is a host directory."""
        return self.kind in self.LOCAL_KINDS

    @property
    def is_git(self) -> bool:
        """True for kinds backed by a git checkout."""
        return self.kind in self.GIT_KINDS

    def local_path(self) -> Optional[str]:
        """The host directory for local kinds, or ``None``."""
        if not self.is_local:
            return None
        if not isinstance(self.path, str) or not self.path.strip():
            return None
        return self.path

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceDescriptor":
        """Parse a raw ``--source`` dict, tolerating missing/junk fields."""

        def _str(key: str) -> Optional[str]:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
            return None

        return cls(
            kind=_str("kind") or "",
            mount_name=_str("mountName") or "",
            path=_str("path"),
            repo_url=_str("repoUrl"),
            ref=_str("ref"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """The wire-protocol dict (only populated fields)."""
        out: Dict[str, Any] = {"kind": self.kind, "mountName": self.mount_name}
        if self.path is not None:
            out["path"] = self.path
        if self.repo_url is not None:
            out["repoUrl"] = self.repo_url
        if self.ref is not None:
            out["ref"] = self.ref
        return out


# --------------------------------------------------------------------------
# Published slots. Setters are the serve host's; getters are everyone's.
# --------------------------------------------------------------------------

_manifest_root: Optional[str] = None
_sources: List[Dict[str, Any]] = []
_project_context: Optional[Any] = None
_orchestrator_spec: Optional[Any] = None
_sandbox_session: Optional[Any] = None
_discard_snapshot_on_exit: bool = False


def set_manifest_root(root: Optional[str]) -> None:
    """Publish the path the agent's tools actually see (``None`` clears)."""
    global _manifest_root
    _manifest_root = root


def get_manifest_root() -> Optional[str]:
    """The published manifest root, or ``None`` outside a serve session."""
    return _manifest_root


def set_sources(
    sources: Optional[Iterable[Union[Mapping[str, Any], SourceDescriptor]]],
) -> None:
    """Publish the parsed ``--source`` descriptor list for this session.

    Accepts the raw dicts that came off the CLI (already JSON-decoded) —
    stored as-is so unknown keys survive — or typed
    :class:`SourceDescriptor` entries. ``None``/empty resets the slot.
    """
    global _sources
    if not sources:
        _sources = []
        return
    _sources = [
        s.to_dict() if isinstance(s, SourceDescriptor) else dict(s) for s in sources
    ]


def get_sources() -> List[Dict[str, Any]]:
    """The published ``--source`` dicts. Empty if unset.

    Returns a shallow copy so callers can't accidentally mutate the
    process-global slot.
    """
    return list(_sources)


def get_source_descriptors() -> List[SourceDescriptor]:
    """The published sources as typed :class:`SourceDescriptor` entries."""
    return [SourceDescriptor.from_dict(s) for s in get_sources()]


def set_project_context(ctx: Optional[Any]) -> None:
    """Publish the resolved ``ProjectContext`` (``None`` clears)."""
    global _project_context
    _project_context = ctx


def get_project_context() -> Optional[Any]:
    """The published ``ProjectContext``, or ``None``."""
    return _project_context


def set_orchestrator_spec(spec: Optional[Any]) -> None:
    """Publish the running orchestrator's ``AgentSpec`` (``None`` clears)."""
    global _orchestrator_spec
    _orchestrator_spec = spec


def get_orchestrator_spec() -> Optional[Any]:
    """The published orchestrator ``AgentSpec``, or ``None``."""
    return _orchestrator_spec


def set_sandbox_session(session: Optional[Any]) -> None:
    """Publish the live ``SandboxSession`` wrapper so server functions that
    need to drive container-level lifecycle (pause/unpause) can reach the
    inner backend session without weaving the reference through every call
    site. The serve host populates this once after ``client.create``/
    ``client.resume`` returns, and clears it on teardown."""
    global _sandbox_session
    _sandbox_session = session


def get_sandbox_session() -> Optional[Any]:
    """The live sandbox session, or ``None`` (host mode / not serving)."""
    return _sandbox_session


def set_discard_snapshot_on_exit(value: bool) -> None:
    """Flag a terminal shutdown: the launcher is closing this session for
    good and deletes the snapshot right after the process exits, so
    teardown should skip the workspace snapshot persist (fingerprint pass
    + full tar export + scrub rewrite). Set by the ``sandbox.
    discard_snapshot`` server function just before SIGTERM."""
    global _discard_snapshot_on_exit
    _discard_snapshot_on_exit = value


def get_discard_snapshot_on_exit() -> bool:
    """True when teardown should skip the snapshot persist."""
    return _discard_snapshot_on_exit


# --------------------------------------------------------------------------
# Sandbox profile switching.
# --------------------------------------------------------------------------


@dataclass
class SwitchContext:
    """Everything ``sandbox.switch`` needs to rebuild the sandbox session
    for a new profile and re-attach it to the already-running agent server,
    plus the observable transition state the ``sandbox.get_state`` /
    ``sandbox.wait_until_ready`` tools report.

    Published once by the serve host after the first bring-up. The
    ``current_*`` / ``started_services`` / ``backend`` / ``container_id``
    slots are mutated in place on each successful switch; ``switching`` /
    ``last_error`` / ``ready_event`` track the in-flight transition.
    """

    # --- wiring to rebuild + re-attach (immutable for the process) ---
    app: Any
    spec: Any
    session_id: str
    snapshot_dir: Optional[Any]  # pathlib.Path | None
    sources_parsed: List[Dict[str, Any]]
    # Boot-time git/CLI credential descriptors ({url, username, tokenEnv});
    # reused on every profile switch so the rebuilt sandbox re-authenticates
    # identically.
    credentials_parsed: List[Dict[str, Any]]
    workspace_dir: Any  # pathlib.Path
    project_id: Optional[str]
    host: str
    port: int
    auth_token: Optional[str]
    # The product's layered profile resolver, keyword-callable as
    # ``resolver(workspace_dir=..., project_id=..., explicit_path=...) ->
    # ResolvedProfile``. ``perform_switch`` / rollback resolve target
    # profiles through it so product layers (packaged default profile,
    # user/project overlays) apply on switches too. ``None`` falls back
    # to the framework resolver
    # (:func:`omniagents.core.sandbox.profiles.resolve_profile`), which
    # has no product default layer.
    profile_resolver: Optional[Any] = None

    # --- current sandbox (mutated per switch) ---
    current_profile_path: Optional[str] = None
    current_profile_name: Optional[str] = None
    backend: Optional[str] = None
    container_id: Optional[str] = None
    service_urls: Dict[str, Any] = field(default_factory=dict)
    started_services: List[Any] = field(default_factory=list)

    # --- transition state ---
    switching: bool = False
    last_error: Optional[str] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Set when the sandbox is ready (idle baseline); cleared while a switch
    # runs.
    ready_event: asyncio.Event = field(default_factory=asyncio.Event)


_switch_context: Optional[SwitchContext] = None


def set_switch_context(ctx: Optional[SwitchContext]) -> None:
    """Publish (or clear, with ``None``) the live switch context. Cleared
    in the serve host's teardown so a stale context never outlives the
    server."""
    global _switch_context
    _switch_context = ctx


def get_switch_context() -> Optional[SwitchContext]:
    """The live switch context, or ``None`` outside a serve session."""
    return _switch_context


def reset() -> None:
    """Clear every slot back to the not-serving defaults (test isolation)."""
    set_manifest_root(None)
    set_sources(None)
    set_project_context(None)
    set_orchestrator_spec(None)
    set_sandbox_session(None)
    set_discard_snapshot_on_exit(False)
    set_switch_context(None)
