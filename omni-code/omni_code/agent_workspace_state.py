"""Process-global state published by ``omni serve`` for tools that need
to see the running session's wiring.

``serve_cli._serve`` populates three slots:

* ``manifest_root`` — the path the agent's tools actually see (e.g.
  ``/workspace`` inside a docker session). Used by server functions like
  ``fs_get_workspace_root``.
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

All slots default to ``None`` so non-serve entry points (tests, direct
invocation) see the absence and can fall back to discovering project.yml
relative to their own location.
"""

from __future__ import annotations

from typing import Any, Optional

_manifest_root: Optional[str] = None
_project_context: Optional[Any] = None
_orchestrator_spec: Optional[Any] = None


def set_manifest_root(root: Optional[str]) -> None:
    global _manifest_root
    _manifest_root = root


def get_manifest_root() -> Optional[str]:
    return _manifest_root


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
