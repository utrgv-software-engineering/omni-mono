"""Embedder seams for the builtin ``workspace_info`` server functions.

The ``fs_*`` server functions (``omniagents.builtin.server_functions.
workspace_info``) describe the workspace the *agent's tools* see, which for
sandboxed launchers differs from the server process's own filesystem. The
launcher owns that wiring; by default it flows through the framework's
process-global serve-session state
(:mod:`omniagents.core.runtime.serve_state`), which any serve host
publishes into. An embedder with different wiring can still install
explicit providers here — an installed provider always wins over the
serve-state default — following the same pattern as
:func:`omniagents.core.runtime.recap.set_recap_summarizer`:

* :func:`set_workspace_sources_provider` — a callable returning the list of
  source descriptors that seeded the workspace (``fs_get_sources``).
  Defaults to :func:`~omniagents.core.runtime.serve_state.get_sources`
  (empty standalone), so the builtin works with no install at all.
* :func:`set_manifest_root_provider` — a callable returning the manifest
  root the agent's tools actually see (e.g. ``/workspace`` inside a docker
  session). Used by ``fs_get_workspace_root`` (fallback) and
  ``fs_list_dir`` (sandbox browse root). Defaults to
  :func:`~omniagents.core.runtime.serve_state.get_manifest_root` (``None``
  standalone) — server functions then fall back to the server's own cwd /
  host filesystem.
* :func:`set_sandbox_session_provider` — a callable returning the live
  sandbox session (the SDK's ``BaseSandboxSession``) when one is running.
  When it yields a session, ``fs_list_dir`` browses the *container*
  filesystem through :class:`omniagents.workspace.SandboxWorkspace`;
  otherwise it lists the host. Defaults to
  :func:`~omniagents.core.runtime.serve_state.get_sandbox_session`
  (``None`` — host mode).

Providers are callables rather than stored values because the underlying
state belongs to the embedder and may change over the server's lifetime
(session restarts, workspace re-seeding). All getters swallow provider
exceptions and return the standalone default — a broken embedder hook must
not take down picker UIs.

Lifted from omni-code (``omni_code/server_functions/filesystem.py`` +
``omni_code/agent_workspace_state.py``).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

__all__ = [
    "set_workspace_sources_provider",
    "workspace_sources_provider",
    "get_workspace_sources",
    "set_manifest_root_provider",
    "manifest_root_provider",
    "get_manifest_root",
    "set_sandbox_session_provider",
    "sandbox_session_provider",
    "get_sandbox_session",
]


_sources_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None
_manifest_root_provider: Optional[Callable[[], Optional[str]]] = None
_sandbox_session_provider: Optional[Callable[[], Optional[Any]]] = None


def set_workspace_sources_provider(
    provider: Optional[Callable[[], List[Dict[str, Any]]]],
) -> None:
    """Install the callable ``fs_get_sources`` reads. ``None`` resets to
    the standalone default (no sources)."""
    global _sources_provider
    _sources_provider = provider


def workspace_sources_provider() -> Optional[Callable[[], List[Dict[str, Any]]]]:
    """The installed sources provider, or ``None``."""
    return _sources_provider


def get_workspace_sources() -> List[Dict[str, Any]]:
    """Source descriptors that seeded the workspace; ``[]`` standalone.

    An installed provider wins; otherwise reads the framework's published
    serve-session state. Returns a shallow copy so callers can't mutate
    the embedder's state.
    """
    if _sources_provider is None:
        from omniagents.core.runtime import serve_state

        try:
            return serve_state.get_sources()
        except Exception:
            return []
    try:
        sources = _sources_provider()
    except Exception:
        return []
    return list(sources) if sources else []


def set_manifest_root_provider(
    provider: Optional[Callable[[], Optional[str]]],
) -> None:
    """Install the callable that yields the manifest root the agent's
    tools see. ``None`` resets to the standalone default (no root)."""
    global _manifest_root_provider
    _manifest_root_provider = provider


def manifest_root_provider() -> Optional[Callable[[], Optional[str]]]:
    """The installed manifest-root provider, or ``None``."""
    return _manifest_root_provider


def get_manifest_root() -> Optional[str]:
    """The manifest root; installed provider wins, else the published
    serve-session state, else ``None``."""
    if _manifest_root_provider is None:
        from omniagents.core.runtime import serve_state

        try:
            root = serve_state.get_manifest_root()
        except Exception:
            return None
    else:
        try:
            root = _manifest_root_provider()
        except Exception:
            return None
    if isinstance(root, str) and root.strip():
        return root
    return None


def set_sandbox_session_provider(
    provider: Optional[Callable[[], Optional[Any]]],
) -> None:
    """Install the callable that yields the live sandbox session. ``None``
    resets to the standalone default (host mode)."""
    global _sandbox_session_provider
    _sandbox_session_provider = provider


def sandbox_session_provider() -> Optional[Callable[[], Optional[Any]]]:
    """The installed sandbox-session provider, or ``None``."""
    return _sandbox_session_provider


def get_sandbox_session() -> Optional[Any]:
    """The live sandbox session; installed provider wins, else the
    published serve-session state, else ``None`` (host mode)."""
    if _sandbox_session_provider is None:
        from omniagents.core.runtime import serve_state

        try:
            return serve_state.get_sandbox_session()
        except Exception:
            return None
    try:
        return _sandbox_session_provider()
    except Exception:
        return None
