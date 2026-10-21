"""First-class sandbox-lifecycle proxies on the session wrapper.

The SDK's :class:`~agents.sandbox.session.sandbox_session.SandboxSession`
proxies a *fixed* method set to the backend session it wraps (held on
``_inner``) and does not forward by ``__getattr__``. The omniagents
extended backends add lifecycle methods the SDK wrapper doesn't know
about — ``pause`` / ``unpause`` (docker cgroup freezer) and pause
introspection (``paused``). Serve hosts used to reach through
``session._inner`` to call them; :class:`LifecycleSandboxSession` makes
them first-class on the wrapper so downstream code never touches
``_inner``.

Every proxy is **capability-probing**: the wrapper works over any
backend, including ones without a lifecycle surface (``unix_local``,
future cloud backends that can only stop+start).

* ``pause`` / ``unpause`` raise :class:`SandboxLifecycleUnsupported`
  when the backend lacks the method — callers that need graceful
  degradation catch that one type (the builtin ``sandbox.*`` server
  functions turn it into ``{"ok": false, "supported": false}``).
* ``paused`` reports ``False`` when the backend can't answer — "not
  paused" is the correct degraded answer for a backend with no pause
  primitive.

The module-level helpers (:func:`resolve_backend_session`,
:func:`backend_supports`) implement the probing over *any* session
object — wrapped or bare — preserving the exact duck-typed semantics
downstream products used before the lift. The unwrap through ``_inner``
is framework-internal (same as ``pty_stream.coerce_pty_streaming``);
downstream code must go through the wrapper methods or these helpers.

The omniagents extended clients return :class:`LifecycleSandboxSession`
from ``create``/``resume`` via :class:`LifecycleSessionClientMixin`, so
any session built through the framework factory carries the proxies.

Lifted from omni-code (``omni_code/server_functions/sandbox.py`` +
``omni_code/serve_cli.py`` ``_inner`` reaches).
"""

from __future__ import annotations

from typing import Any

from agents.sandbox.session.base_sandbox_session import BaseSandboxSession
from agents.sandbox.session.sandbox_session import SandboxSession

__all__ = [
    "SandboxLifecycleUnsupported",
    "LifecycleSandboxSession",
    "LifecycleSessionClientMixin",
    "resolve_backend_session",
    "backend_supports",
]


class SandboxLifecycleUnsupported(RuntimeError):
    """The session's backend doesn't implement the requested lifecycle op."""


def resolve_backend_session(session: Any) -> Any:
    """Unwrap the SDK ``SandboxSession`` proxy to the backend session.

    Framework-internal: callers outside omniagents should use the
    :class:`LifecycleSandboxSession` methods instead. Objects without an
    ``_inner`` (bare backend sessions, test fakes) pass through unchanged
    — the exact semantics of the historical downstream
    ``getattr(session, "_inner", session)`` reach.
    """
    return getattr(session, "_inner", session)


def backend_supports(session: Any, op: str) -> bool:
    """True when *session*'s backend implements lifecycle method *op*."""
    return hasattr(resolve_backend_session(session), op)


class LifecycleSandboxSession(SandboxSession):
    """SDK session wrapper plus first-class lifecycle proxies.

    Constructed by :class:`LifecycleSessionClientMixin` (all omniagents
    extended sandbox clients); a drop-in :class:`SandboxSession` for
    every existing caller.
    """

    # ----------------------------------------------------------------- #
    # Pause / unpause (docker cgroup freezer et al.)
    # ----------------------------------------------------------------- #

    def supports_pause(self) -> bool:
        return hasattr(self._inner, "pause")

    def supports_unpause(self) -> bool:
        return hasattr(self._inner, "unpause")

    async def pause(self) -> None:
        """Freeze every process in the sandbox. Raises
        :class:`SandboxLifecycleUnsupported` when the backend has no
        pause primitive; backend errors propagate as-is."""
        inner = self._inner
        if not hasattr(inner, "pause"):
            raise SandboxLifecycleUnsupported("backend does not implement pause")
        await inner.pause()

    async def unpause(self) -> None:
        """Thaw a previously-paused sandbox. Raises
        :class:`SandboxLifecycleUnsupported` when the backend has no
        unpause primitive; backend errors propagate as-is."""
        inner = self._inner
        if not hasattr(inner, "unpause"):
            raise SandboxLifecycleUnsupported("backend does not implement unpause")
        await inner.unpause()

    async def paused(self) -> bool:
        """Best-effort paused report. ``False`` when the backend can't
        answer (no ``paused()`` or it errored) — a backend without a
        pause primitive is by definition not paused."""
        inner = self._inner
        if not hasattr(inner, "paused"):
            return False
        try:
            return bool(await inner.paused())
        except Exception:
            return False

    # ----------------------------------------------------------------- #
    # Network toggle (wasm gates in-process; docker toggles via iptables
    # in the lifecycle controller and never lands here)
    # ----------------------------------------------------------------- #

    @property
    def network_toggleable(self) -> bool:
        """True when the backend supports a live egress toggle."""
        return bool(getattr(self._inner, "network_toggleable", False))

    @property
    def network_enabled(self) -> bool:
        """Current toggle state; backends without a toggle read enabled."""
        return bool(getattr(self._inner, "network_enabled", True))

    async def set_network_enabled(self, enabled: bool) -> None:
        """Flip the backend's live egress gate. Raises
        :class:`SandboxLifecycleUnsupported` when the backend has no
        toggle; backend errors propagate as-is."""
        inner = self._inner
        if not hasattr(inner, "set_network_enabled"):
            raise SandboxLifecycleUnsupported(
                "backend does not implement a network toggle"
            )
        await inner.set_network_enabled(enabled)

    # ----------------------------------------------------------------- #
    # Introspection
    # ----------------------------------------------------------------- #

    @property
    def backend_class_name(self) -> str:
        """The backend session's class name (diagnostics/labels only)."""
        return type(self._inner).__name__


class LifecycleSessionClientMixin:
    """Wrap sessions in :class:`LifecycleSandboxSession`.

    Mixed into the omniagents extended sandbox clients (docker,
    unix_local) ahead of the SDK base so their
    ``create``/``resume`` paths — which all funnel through the SDK's
    ``_wrap_session`` — hand back the lifecycle-capable wrapper.
    """

    def _wrap_session(
        self,
        inner: BaseSandboxSession,
        *,
        instrumentation: Any | None = None,
    ) -> SandboxSession:
        return LifecycleSandboxSession(
            inner,
            instrumentation=instrumentation,
            dependencies=self._resolve_dependencies(),  # type: ignore[attr-defined]
        )
