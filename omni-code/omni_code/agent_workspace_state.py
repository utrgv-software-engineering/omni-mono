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
