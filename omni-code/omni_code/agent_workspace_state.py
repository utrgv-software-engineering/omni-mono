"""Process-global state published by ``omni serve`` for tools that need
to see the running session's wiring.

``serve_cli._serve`` populates four slots:

* ``manifest_root`` — the path the agent's tools actually see (e.g.
  ``/workspace`` inside a docker session). Used by server functions like
  ``fs_get_workspace_root``.
* ``sources`` — the parsed ``--source`` descriptors that seeded the
  workspace. Each entry has ``kind`` (``local``/``local-git``/``git-remote``),
  ``mountName``, and either ``path`` (host dir for local kinds) or
  ``repoUrl`` (+ optional ``ref``) for git-remote. Used by
  ``fs_get_sources`` and the agent's context factory so multi-source
  projects can advertise every directory in play — important under
  host-mode (``unix_local``) where sources are NOT relocated onto the
  primary workspace and would otherwise be invisible to the agent.
* ``project_context`` — the resolved ``ProjectContext`` from
  ``load_project``. Used by ``tools/worker_tools._load_and_configure_spec``
  to build the worker spec through the same code path as the orchestrator
  (``build_spec_from_project``), so the worker inherits the full
  capability/tool/guardrail registries instead of loading its YAML in
  isolation with everything skipped.
* ``orchestrator_spec`` — the running orchestrator's ``AgentSpec`` after
  ``_attach_session_to_spec`` has wired the live ``SandboxSession``. Used
  by the worker spec loader to forward ``sandbox_use_sandbox_agent``,
  ``sandbox_run_as`` and ``sandbox_run_config`` so worker tools route
  through the same sandbox.

All slots default to ``None``/empty so non-serve entry points (tests,
direct invocation) see the absence and can fall back to discovering
project.yml relative to their own location.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_manifest_root: Optional[str] = None
_sources: List[Dict[str, Any]] = []
_project_context: Optional[Any] = None
_orchestrator_spec: Optional[Any] = None
_sandbox_session: Optional[Any] = None


def set_manifest_root(root: Optional[str]) -> None:
    global _manifest_root
    _manifest_root = root


def get_manifest_root() -> Optional[str]:
    return _manifest_root


def set_sources(sources: Optional[List[Dict[str, Any]]]) -> None:
    """Store the parsed ``--source`` descriptor list for this serve session.

    Accepts the raw dicts that came off the CLI (already JSON-decoded), so
    we don't need to depend on the SDK's typed entries. Passing ``None``
    or an empty list resets the slot.
    """
    global _sources
    _sources = list(sources) if sources else []


def get_sources() -> List[Dict[str, Any]]:
    """Return the parsed ``--source`` descriptor list. Empty if unset.

    Returns a shallow copy so callers can't accidentally mutate the
    process-global slot.
    """
    return list(_sources)


def set_project_context(ctx: Optional[Any]) -> None:
    global _project_context
    _project_context = ctx


def get_project_context() -> Optional[Any]:
    return _project_context


def set_orchestrator_spec(spec: Optional[Any]) -> None:
    global _orchestrator_spec
    _orchestrator_spec = spec


def get_orchestrator_spec() -> Optional[Any]:
    return _orchestrator_spec


def set_sandbox_session(session: Optional[Any]) -> None:
    """Publish the live ``SandboxSession`` wrapper so server functions that
    need to drive container-level lifecycle (``sandbox.pause`` / ``sandbox.unpause``)
    can reach the inner backend session without weaving the reference
    through every call site. ``serve_cli._serve`` populates this once
    after ``client.create``/``client.resume`` returns."""
    global _sandbox_session
    _sandbox_session = session


def get_sandbox_session() -> Optional[Any]:
    return _sandbox_session


_discard_snapshot_on_exit: bool = False


def set_discard_snapshot_on_exit(value: bool) -> None:
    """Flag a terminal shutdown: the launcher is closing this session for
    good and deletes the snapshot tar right after the process exits, so
    ``teardown_session`` should skip the workspace snapshot persist
    (fingerprint pass + full tar export + scrub rewrite). Set by the
    ``sandbox.discard_snapshot`` server function just before SIGTERM."""
    global _discard_snapshot_on_exit
    _discard_snapshot_on_exit = value


def get_discard_snapshot_on_exit() -> bool:
    return _discard_snapshot_on_exit


@dataclass
class SwitchContext:
    """Everything ``sandbox.switch`` needs to rebuild the SandboxSession for a
    new profile and re-attach it to the already-running agent server, plus the
    observable transition state the ``sandbox.get_state`` /
    ``sandbox.wait_until_ready`` tools report.

    Published once by ``serve_cli._serve`` after the first bring-up. The
    ``current_*`` / ``started_services`` / ``backend`` / ``container_id`` slots
    are mutated in place on each successful switch; ``switching`` / ``last_error``
    / ``ready_event`` track the in-flight transition.
    """

    # --- wiring to rebuild + re-attach (immutable for the process) ---
    app: Any
    spec: Any
    session_id: str
    snapshot_dir: Optional[Any]  # pathlib.Path | None
    sources_parsed: List[Dict[str, Any]]
    # Boot-time git/CLI credential descriptors ({url, username, tokenEnv}); reused
    # on every profile switch so the rebuilt sandbox re-authenticates identically.
    credentials_parsed: List[Dict[str, Any]]
    workspace_dir: Any  # pathlib.Path
    project_id: Optional[str]
    host: str
    port: int
    auth_token: Optional[str]

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
    # Set when the sandbox is ready (idle baseline); cleared while a switch runs.
    ready_event: asyncio.Event = field(default_factory=asyncio.Event)


_switch_context: Optional[SwitchContext] = None


def set_switch_context(ctx: Optional["SwitchContext"]) -> None:
    """Publish (or clear, with ``None``) the live switch context. Cleared in
    ``_serve``'s teardown so a stale context never outlives the server."""
    global _switch_context
    _switch_context = ctx


def get_switch_context() -> Optional["SwitchContext"]:
    return _switch_context
