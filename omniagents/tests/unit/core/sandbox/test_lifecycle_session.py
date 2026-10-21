"""Tests for the first-class lifecycle proxies on the session wrapper
(:mod:`omniagents.core.sandbox.lifecycle_session`)."""

from __future__ import annotations

import asyncio

import pytest

from omniagents.core.sandbox.lifecycle_session import (
    LifecycleSandboxSession,
    LifecycleSessionClientMixin,
    SandboxLifecycleUnsupported,
    backend_supports,
    resolve_backend_session,
)


class _BareInner:
    """Backend session with NO lifecycle surface (unix_local-like)."""

    def set_dependencies(self, dependencies):  # SDK wrapper calls this
        self.dependencies = dependencies


class _LifecycleInner(_BareInner):
    """Backend session with the full docker-extended lifecycle surface."""

    def __init__(self):
        self.calls = []
        self._paused = False

    async def pause(self):
        self.calls.append("pause")
        self._paused = True

    async def unpause(self):
        self.calls.append("unpause")
        self._paused = False

    async def paused(self):
        return self._paused


def _wrap(inner) -> LifecycleSandboxSession:
    return LifecycleSandboxSession(inner)


# ------------------------- supported backend --------------------------- #


def test_proxies_forward_to_backend():
    inner = _LifecycleInner()
    session = _wrap(inner)

    async def go():
        await session.pause()
        assert await session.paused() is True
        await session.unpause()
        assert await session.paused() is False

    asyncio.run(go())
    assert inner.calls == ["pause", "unpause"]
    assert session.supports_pause() and session.supports_unpause()
    assert session.backend_class_name == "_LifecycleInner"


def test_backend_errors_propagate():
    class _Failing(_LifecycleInner):
        async def pause(self):
            raise RuntimeError("freezer says no")

    session = _wrap(_Failing())
    with pytest.raises(RuntimeError, match="freezer says no"):
        asyncio.run(session.pause())


# ------------------------ unsupported backend -------------------------- #


def test_unsupported_backend_probes():
    session = _wrap(_BareInner())

    assert session.supports_pause() is False
    assert session.supports_unpause() is False
    with pytest.raises(SandboxLifecycleUnsupported):
        asyncio.run(session.pause())
    with pytest.raises(SandboxLifecycleUnsupported):
        asyncio.run(session.unpause())
    # Best-effort ops no-op instead of raising.
    assert asyncio.run(session.paused()) is False


def test_paused_swallows_backend_errors():
    class _Flaky(_BareInner):
        async def paused(self):
            raise RuntimeError("gone")

    assert asyncio.run(_wrap(_Flaky()).paused()) is False


# ----------------------- duck-typed module helpers --------------------- #


def test_resolve_backend_session_unwraps_wrapper():
    inner = _LifecycleInner()
    session = _wrap(inner)
    assert resolve_backend_session(session) is inner
    # Bare objects pass through unchanged (historical downstream semantics).
    bare = _BareInner()
    assert resolve_backend_session(bare) is bare


def test_backend_supports_probes_through_wrapper():
    assert backend_supports(_wrap(_LifecycleInner()), "pause") is True
    assert backend_supports(_wrap(_BareInner()), "pause") is False
    assert backend_supports(_LifecycleInner(), "unpause") is True


# --------------------------- client wiring ----------------------------- #


def test_mixin_wraps_in_lifecycle_session():
    class _Client(LifecycleSessionClientMixin):
        def _resolve_dependencies(self):
            return None

    session = _Client()._wrap_session(_LifecycleInner())
    assert isinstance(session, LifecycleSandboxSession)


def test_all_extended_clients_carry_the_mixin():
    from omniagents.core.sandbox.docker_extended import ExtendedDockerSandboxClient
    from omniagents.core.sandbox.host_bridge import HostBridgeSandboxClient
    from omniagents.core.sandbox.unix_local_extended import (
        ExtendedUnixLocalSandboxClient,
    )

    for cls in (
        ExtendedDockerSandboxClient,
        ExtendedUnixLocalSandboxClient,
        HostBridgeSandboxClient,
    ):
        assert issubclass(cls, LifecycleSessionClientMixin), cls
