"""Tests for sandbox-owned idle pause on ``ExtendedDockerSandboxSession``.

Pause is intentionally a sandbox-level concern: any call into the session
auto-unpauses, a background watcher freezes the container after a
configurable threshold of inactivity. The contract:

* ``start_idle_watcher(threshold_s)`` arms the watcher; ``threshold_s<=0``
  is a no-op (disabled).
* The watcher pauses after ``threshold_s`` seconds of no calls.
* Any wrapped entry point (``_exec_run``, ``_stream_into_exec``,
  ``pty_exec_start``, ``pty_write_stdin``) calls ``_touch_activity``,
  which resets the timer and auto-unpauses if currently paused.
* ``stop_idle_watcher`` cancels the watcher AND unpauses, so snapshot
  persist / shutdown don't hang on a frozen container.

We don't go near a real docker daemon — the parent ``_exec_run`` /
``_stream_into_exec`` / ``pty_*`` implementations are monkey-patched to
no-ops on a session instance built without ``__init__`` running, so we
exercise only the wrappers we added.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from agents.sandbox.sandboxes.docker import DockerSandboxSession
from omniagents.core.sandbox.docker_extended import ExtendedDockerSandboxSession


def _make_session() -> ExtendedDockerSandboxSession:
    """Build a minimally-wired session whose pause/unpause / wrapped exec
    methods are stubbed so tests can drive lifecycle without docker.

    We bypass ``__init__`` (it needs a real Container / DockerClient) and
    set the small set of attributes the wrappers and watcher actually
    touch. Then we monkey-patch the parent ``_exec_run`` etc. on the
    INSTANCE so ``super()._exec_run(...)`` works.
    """
    from omniagents.core.sandbox.docker_extended import DEFAULT_IDLE_THRESHOLD_S

    session = ExtendedDockerSandboxSession.__new__(ExtendedDockerSandboxSession)
    # Idle state — mirrors what __init__ would set.
    session._idle_threshold_s = float(DEFAULT_IDLE_THRESHOLD_S)
    session._idle_last_activity_at = time.monotonic()
    session._idle_watcher_task = None
    session._idle_paused = False
    session._idle_paused_at = None
    session._idle_lock = asyncio.Lock()

    # Counters for stubbed surfaces.
    session._test_pause_calls = 0
    session._test_unpause_calls = 0
    session._test_exec_calls = 0
    session._test_stream_calls = 0
    session._test_pty_start_calls = 0
    session._test_pty_write_calls = 0
    session._test_ensure_started_calls = 0
    # Controls what the stubbed ``paused()`` reports (the real one reloads
    # the container and reads docker status). Tests flip this to model a
    # frozen container.
    session._test_paused_returns = False

    async def _stub_pause():
        session._test_pause_calls += 1

    async def _stub_unpause():
        session._test_unpause_calls += 1

    async def _stub_paused():
        return session._test_paused_returns

    # Override pause/unpause/paused on the instance — these talk to docker in
    # real life; tests just count / control them.
    session.pause = _stub_pause  # type: ignore[method-assign]
    session.unpause = _stub_unpause  # type: ignore[method-assign]
    session.paused = _stub_paused  # type: ignore[method-assign]

    # Stub the parent implementations of every wrapped method so
    # ``super().<method>(...)`` resolves to a counter. We can't patch
    # ``super()._exec_run`` directly, so instead patch on the parent
    # class via a temporary attribute that shadows the SDK impl.
    async def _stub_exec_run(*_args, **_kwargs):
        session._test_exec_calls += 1
        return "ok"

    async def _stub_stream(*_args, **_kwargs):
        session._test_stream_calls += 1
        return None

    async def _stub_pty_start(*_args, **_kwargs):
        session._test_pty_start_calls += 1
        return "pty_started"

    async def _stub_pty_write(*_args, **_kwargs):
        session._test_pty_write_calls += 1
        return "pty_wrote"

    async def _stub_ensure_started(*_args, **_kwargs):
        session._test_ensure_started_calls += 1

    # Monkey-patch the parent (SDK) class methods so super() resolves
    # to our stubs for this test run. We restore in the fixture teardown.
    DockerSandboxSession._exec_run = _stub_exec_run  # type: ignore[assignment]
    DockerSandboxSession._stream_into_exec = _stub_stream  # type: ignore[assignment]
    DockerSandboxSession.pty_exec_start = _stub_pty_start  # type: ignore[assignment]
    DockerSandboxSession.pty_write_stdin = _stub_pty_write  # type: ignore[assignment]
    DockerSandboxSession._ensure_backend_started = _stub_ensure_started  # type: ignore[assignment]

    return session


@pytest.fixture
def session():
    s = _make_session()
    yield s
    # Best-effort restore — none of these methods are called outside the
    # test suite in a process that loaded these patches, but resetting
    # avoids cross-test bleed if any other test imports
    # ``DockerSandboxSession`` directly.
    for attr in (
        "_exec_run",
        "_stream_into_exec",
        "pty_exec_start",
        "pty_write_stdin",
        "_ensure_backend_started",
    ):
        if hasattr(DockerSandboxSession, attr):
            try:
                delattr(DockerSandboxSession, attr)
            except AttributeError:
                pass


# ---------------------------------------------------------------------------
# Default-on behavior — watcher arms itself in _after_start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_start_arms_watcher_by_default(session, monkeypatch):
    """The watcher should be on by default — no embedder opt-in required.
    Verifies that ``_after_start`` starts the watcher when the threshold
    is its default value."""
    # Stub the parent _after_start to avoid touching workspace_root_ready.
    async def _parent_after_start_noop(self_):
        return None

    monkeypatch.setattr(
        "agents.sandbox.sandboxes.docker.DockerSandboxSession._after_start",
        _parent_after_start_noop,
    )
    monkeypatch.setattr(
        "omniagents.core.sandbox.docker_extended._IDLE_WATCHER_INTERVAL_S", 0.02
    )
    # Default threshold is 15 min — that's set in _make_session. Run
    # _after_start and verify the watcher task exists.
    await session._after_start()
    assert session._idle_watcher_task is not None
    assert not session._idle_watcher_task.done()
    await session.stop_idle_watcher()


@pytest.mark.asyncio
async def test_after_start_skips_watcher_when_threshold_zero(session, monkeypatch):
    """Embedders that explicitly disable via ``set_idle_threshold(0)``
    before ``start()`` should NOT get a watcher armed."""
    async def _parent_after_start_noop(self_):
        return None

    monkeypatch.setattr(
        "agents.sandbox.sandboxes.docker.DockerSandboxSession._after_start",
        _parent_after_start_noop,
    )
    session.set_idle_threshold(0)
    await session._after_start()
    assert session._idle_watcher_task is None


@pytest.mark.asyncio
async def test_set_idle_threshold_clamps_negative_to_zero(session):
    session.set_idle_threshold(-5)
    assert session._idle_threshold_s == 0.0


@pytest.mark.asyncio
async def test_set_idle_threshold_coerces_to_float(session):
    session.set_idle_threshold(60)
    assert session._idle_threshold_s == 60.0
    assert isinstance(session._idle_threshold_s, float)


# ---------------------------------------------------------------------------
# start_idle_watcher / stop_idle_watcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_watcher_noop_when_disabled(session):
    session.start_idle_watcher(0)
    assert session._idle_watcher_task is None
    await session.stop_idle_watcher()  # idempotent


@pytest.mark.asyncio
async def test_start_watcher_noop_when_negative(session):
    session.start_idle_watcher(-1)
    assert session._idle_watcher_task is None


@pytest.mark.asyncio
async def test_start_watcher_idempotent(session, monkeypatch):
    monkeypatch.setattr(
        "omniagents.core.sandbox.docker_extended._IDLE_WATCHER_INTERVAL_S", 1.0
    )
    session.start_idle_watcher(60)
    first = session._idle_watcher_task
    assert first is not None
    session.start_idle_watcher(60)
    assert session._idle_watcher_task is first  # no replacement
    await session.stop_idle_watcher()


# ---------------------------------------------------------------------------
# _touch_activity + auto-unpause
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_touch_activity_updates_timestamp(session):
    session._idle_last_activity_at = 0.0
    await session._touch_activity()
    assert session._idle_last_activity_at > 0.0


@pytest.mark.asyncio
async def test_touch_activity_auto_unpauses_when_paused(session):
    session._idle_paused = True
    session._idle_paused_at = time.monotonic() - 5
    await session._touch_activity()
    assert session._test_unpause_calls == 1
    assert session._idle_paused is False


@pytest.mark.asyncio
async def test_touch_activity_cheap_when_not_paused(session):
    await session._touch_activity()
    await session._touch_activity()
    assert session._test_unpause_calls == 0


@pytest.mark.asyncio
async def test_touch_activity_clears_flag_even_when_unpause_fails(session):
    async def _failing_unpause():
        raise RuntimeError("simulated unpause failure")

    session.unpause = _failing_unpause  # type: ignore[method-assign]
    session._idle_paused = True
    session._idle_paused_at = time.monotonic()
    await session._touch_activity()
    # Flag cleared so future calls don't loop forever trying to unpause.
    assert session._idle_paused is False


# ---------------------------------------------------------------------------
# Wrapped entry points reset timer + auto-unpause
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exec_run_wrapper_touches_activity(session):
    session._idle_paused = True
    session._idle_paused_at = time.monotonic()
    result = await session._exec_run(
        cmd=["ls"],
        workdir=None,
        user=None,
        timeout=None,
        command_for_errors=("ls",),
        kill_on_timeout=False,
    )
    assert result == "ok"
    assert session._test_exec_calls == 1
    assert session._test_unpause_calls == 1
    assert session._idle_paused is False


@pytest.mark.asyncio
async def test_stream_into_exec_wrapper_touches_activity(session):
    session._idle_paused = True
    session._idle_paused_at = time.monotonic()
    import io as _io

    await session._stream_into_exec(
        cmd=["cat"],
        stream=_io.BytesIO(b"data"),
        error_path=None,
        user=None,
    )
    assert session._test_stream_calls == 1
    assert session._test_unpause_calls == 1


@pytest.mark.asyncio
async def test_pty_exec_start_wrapper_touches_activity(session):
    session._idle_paused = True
    session._idle_paused_at = time.monotonic()
    result = await session.pty_exec_start("bash")
    assert result == "pty_started"
    assert session._test_pty_start_calls == 1
    assert session._test_unpause_calls == 1


@pytest.mark.asyncio
async def test_pty_write_stdin_wrapper_touches_activity(session):
    session._idle_paused = True
    session._idle_paused_at = time.monotonic()
    result = await session.pty_write_stdin(session_id=1, chars="hi")
    assert result == "pty_wrote"
    assert session._test_pty_write_calls == 1
    assert session._test_unpause_calls == 1


# ---------------------------------------------------------------------------
# Watcher behavior end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watcher_pauses_after_threshold(session, monkeypatch):
    monkeypatch.setattr(
        "omniagents.core.sandbox.docker_extended._IDLE_WATCHER_INTERVAL_S", 0.02
    )
    # Threshold 50ms; seed last_activity_at far enough in the past to fire
    # on the first watcher tick.
    session.start_idle_watcher(0.05)
    session._idle_last_activity_at = time.monotonic() - 1.0
    for _ in range(40):
        await asyncio.sleep(0.01)
        if session._test_pause_calls > 0:
            break
    await session.stop_idle_watcher()
    assert session._test_pause_calls == 1
    # stop_idle_watcher unpaused on the way out.
    assert session._test_unpause_calls == 1
    assert session._idle_paused is False


@pytest.mark.asyncio
async def test_watcher_resets_on_wrapped_call(session, monkeypatch):
    """If a wrapped method fires before the threshold, the timer resets and
    the watcher does not pause. We hammer ``_exec_run`` on a short interval
    so the threshold never elapses without activity."""
    monkeypatch.setattr(
        "omniagents.core.sandbox.docker_extended._IDLE_WATCHER_INTERVAL_S", 0.02
    )
    session.start_idle_watcher(0.10)
    end = time.monotonic() + 0.30
    while time.monotonic() < end:
        await session._exec_run(
            cmd=["true"],
            workdir=None,
            user=None,
            timeout=None,
            command_for_errors=("true",),
            kill_on_timeout=False,
        )
        await asyncio.sleep(0.02)
    await session.stop_idle_watcher()
    assert session._test_pause_calls == 0


@pytest.mark.asyncio
async def test_stop_idle_watcher_unpauses_on_the_way_out(session, monkeypatch):
    """The teardown contract: if we were paused when stop() is called,
    unpause synchronously before returning so snapshot persist / shutdown
    don't hang."""
    monkeypatch.setattr(
        "omniagents.core.sandbox.docker_extended._IDLE_WATCHER_INTERVAL_S", 0.02
    )
    session.start_idle_watcher(0.05)
    session._idle_last_activity_at = time.monotonic() - 1.0
    for _ in range(40):
        await asyncio.sleep(0.01)
        if session._idle_paused:
            break
    assert session._idle_paused is True
    await session.stop_idle_watcher()
    assert session._idle_paused is False
    assert session._test_unpause_calls == 1


@pytest.mark.asyncio
async def test_stop_idle_watcher_idempotent(session):
    await session.stop_idle_watcher()
    await session.stop_idle_watcher()
    # No exceptions, no spurious unpauses.
    assert session._test_unpause_calls == 0


# ---------------------------------------------------------------------------
# _ensure_backend_started: bring-up thaws a frozen container
#
# Regression for the 409 "cannot start a paused container, try unpause
# instead": the idle watcher freezes the container, the next turn re-ensures
# the session, and the SDK base reads a paused container as "not running" and
# calls container.start(). Our override must unpause before delegating.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_backend_started_unpauses_watcher_paused(session):
    """Watcher-paused (in-memory flag set): ``_touch_activity`` thaws it via
    the idle state machine, then the base start runs against a live
    container. No 409."""
    session._idle_paused = True
    session._idle_paused_at = time.monotonic() - 5
    # After _touch_activity unpauses, docker is no longer paused.
    session._test_paused_returns = False

    await session._ensure_backend_started()

    assert session._test_unpause_calls == 1  # via _touch_activity
    assert session._idle_paused is False
    assert session._test_ensure_started_calls == 1  # base start still ran


@pytest.mark.asyncio
async def test_ensure_backend_started_unpauses_out_of_band_pause(session):
    """Container frozen out-of-band (manual ``docker pause`` / desynced
    flag): ``_touch_activity`` no-ops because the flag is clear, but the
    defensive ``paused()`` re-check still thaws it before the base start."""
    session._idle_paused = False
    session._test_paused_returns = True

    await session._ensure_backend_started()

    assert session._test_unpause_calls == 1  # via the defensive paused() check
    assert session._test_ensure_started_calls == 1


@pytest.mark.asyncio
async def test_ensure_backend_started_no_unpause_when_running(session):
    """Healthy / stopped container (not paused): no spurious unpause, timer
    reset, base start runs."""
    session._idle_last_activity_at = 0.0
    session._idle_paused = False
    session._test_paused_returns = False

    await session._ensure_backend_started()

    assert session._test_unpause_calls == 0
    assert session._test_ensure_started_calls == 1
    assert session._idle_last_activity_at > 0.0  # bring-up counts as activity
