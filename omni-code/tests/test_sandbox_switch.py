"""Unit tests for in-place sandbox switching (``perform_switch`` + the
observation tools). Backend bring-up/teardown are mocked — these exercise the
orchestration (locking, ordering, re-wire, broadcasts, rollback, ctx mutation),
which is the part that doesn't need a real docker/aci environment.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from omni_code import serve_cli
from omniagents.core.runtime import serve_state as aws


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeProfile:
    def __init__(self, backend="docker"):
        self.raw = {"client": {"type": backend}}
        self.terminal = object()


class FakeSession:
    def __init__(self, name):
        self.name = name

    async def resolve_exposed_port(self, port):
        return SimpleNamespace(host="127.0.0.1", port=port)


class FakeBrought:
    def __init__(self, session, backend, container_id):
        self.session = session
        self.started_services = [("svc", 8080)]
        self.service_urls = {"code_server": f"http://x/{container_id}"}
        self.resume_tier = "rehydrated"
        self.manifest = object()
        self.client_type = backend
        self.container_id = container_id


class FakeSessionManager:
    def __init__(self):
        self._task_map = {}
        self.cancelled = []

    async def cancel(self, run_id):
        self.cancelled.append(run_id)
        self._task_map.pop(run_id, None)


class FakeService:
    """Mirrors the AgentService surface perform_switch touches: the public
    run-quiesce API (active_run_ids/stop_run) and client-function broadcasts."""

    def __init__(self):
        self._session_manager = FakeSessionManager()
        self.events = []  # (function, args)

    def active_run_ids(self):
        return list(self._session_manager._task_map)

    async def stop_run(self, run_id):
        await self._session_manager.cancel(run_id)
        return True

    async def request_client_function(
        self, function, args=None, *, session=None, expect_result=False, **kw
    ):
        self.events.append((function, args))
        return None


def _make_ctx(current_path="/profiles/host.yml", current_name="host"):
    ctx = aws.SwitchContext(
        app=object(),
        spec=object(),
        session_id="sess-1",
        snapshot_dir=None,
        sources_parsed=[],
        credentials_parsed=[],
        workspace_dir=__import__("pathlib").Path("/tmp/ws"),
        project_id=None,
        host="127.0.0.1",
        port=3001,
        auth_token=None,
        current_profile_path=current_path,
        current_profile_name=current_name,
        backend="unix_local",
        container_id=None,
        service_urls={},
        started_services=[("old", 1)],
    )
    ctx.ready_event.set()
    return ctx


@pytest.mark.asyncio
async def test_resolve_service_urls_points_vnc_at_novnc_client():
    urls = await serve_cli._resolve_service_urls(
        FakeSession("s1"),
        [
            (
                serve_cli.ServiceSpec(
                    name="code_server", command="code-server", port=8080
                ),
                1,
            ),
            (serve_cli.ServiceSpec(name="vnc", command="start-vnc", port=6080), 2),
        ],
    )

    assert urls == {
        "code_server": "http://127.0.0.1:8080",
        "vnc": "http://127.0.0.1:6080/vnc.html?autoconnect=true&resize=scale",
    }


@pytest.fixture
def wired(monkeypatch):
    """Install a fake service + record re-wire calls; restore globals after."""
    service = FakeService()
    monkeypatch.setattr(
        "omniagents.core.agents.service.current_service", lambda: service
    )

    calls = {"teardown": [], "attach": [], "terminal": [], "orchestrator": []}

    async def fake_teardown(session, started_services):
        calls["teardown"].append((session, started_services))

    monkeypatch.setattr(serve_cli, "teardown_session", fake_teardown)
    monkeypatch.setattr(
        serve_cli,
        "resolve_profile",
        lambda **kw: FakeProfile(backend="docker"),
    )
    monkeypatch.setattr(
        serve_cli,
        "_attach_session_to_spec",
        lambda spec, session, raw: calls["attach"].append(session),
    )
    monkeypatch.setattr(
        serve_cli,
        "_install_session_terminal_manager",
        lambda app, session, terminal: calls["terminal"].append(session),
    )
    monkeypatch.setattr(
        "omniagents.core.runtime.serve_state.set_orchestrator_spec",
        lambda spec: calls["orchestrator"].append(spec),
    )

    # Reset the published globals for a clean slate.
    aws.set_sandbox_session(None)
    aws.set_switch_context(None)
    yield service, calls
    aws.set_sandbox_session(None)
    aws.set_switch_context(None)


# --------------------------------------------------------------------------
# perform_switch
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switch_happy_path_rewires_and_broadcasts(wired, monkeypatch):
    service, calls = wired
    old = FakeSession("old")
    new = FakeSession("new")
    aws.set_sandbox_session(old)
    aws.set_switch_context(_make_ctx())

    async def fake_bring_up(**kw):
        assert kw["prior_container_id"] is None  # cross-backend → no warm reattach
        return FakeBrought(new, "docker", "ctr-new")

    monkeypatch.setattr(serve_cli, "bring_up_session", fake_bring_up)

    res = await serve_cli.perform_switch("/profiles/devbox.yml")

    assert res["ok"] is True
    assert res["profile"] == "devbox"
    assert res["backend"] == "docker"
    assert res["container_id"] == "ctr-new"
    # Old torn down, new wired in.
    assert calls["teardown"] == [(old, [("old", 1)])]
    assert calls["attach"] == [new]
    assert calls["terminal"] == [new]
    assert aws.get_sandbox_session() is new
    # ctx updated.
    ctx = aws.get_switch_context()
    assert ctx.current_profile_name == "devbox"
    assert ctx.container_id == "ctr-new"
    assert ctx.switching is False and ctx.ready_event.is_set()
    # Broadcast switching → changed, in order.
    fns = [e[0] for e in service.events]
    assert fns == ["ui.sandbox.switching", "ui.sandbox.changed"]


@pytest.mark.asyncio
async def test_switch_noop_when_same_profile(wired, monkeypatch):
    service, calls = wired
    aws.set_sandbox_session(FakeSession("old"))
    aws.set_switch_context(
        _make_ctx(current_path="/profiles/devbox.yml", current_name="devbox")
    )
    monkeypatch.setattr(serve_cli, "bring_up_session", None)  # must not be called

    res = await serve_cli.perform_switch("/profiles/devbox.yml")
    assert res["ok"] is True and res["profile"] == "devbox"
    assert calls["teardown"] == []  # nothing torn down
    assert service.events == []  # no broadcast


@pytest.mark.asyncio
async def test_switch_quiesces_active_run(wired, monkeypatch):
    service, calls = wired
    service._session_manager._task_map["run-42"] = object()
    aws.set_sandbox_session(FakeSession("old"))
    aws.set_switch_context(_make_ctx())
    monkeypatch.setattr(
        serve_cli,
        "bring_up_session",
        lambda **kw: _coro(FakeBrought(FakeSession("new"), "docker", "c")),
    )

    await serve_cli.perform_switch("/profiles/devbox.yml")
    assert service._session_manager.cancelled == ["run-42"]


@pytest.mark.asyncio
async def test_switch_rolls_back_on_bringup_failure(wired, monkeypatch):
    service, calls = wired
    old = FakeSession("old")
    rolled = FakeSession("rolled")
    aws.set_sandbox_session(old)
    aws.set_switch_context(
        _make_ctx(current_path="/profiles/host.yml", current_name="host")
    )

    state = {"n": 0}

    async def fake_bring_up(**kw):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("docker daemon down")
        return FakeBrought(rolled, "unix_local", None)  # rollback bring-up

    monkeypatch.setattr(serve_cli, "bring_up_session", fake_bring_up)

    res = await serve_cli.perform_switch("/profiles/devbox.yml")
    assert res["ok"] is False
    assert "rolled back" in res["reason"]
    # Rolled back to the previous session.
    assert aws.get_sandbox_session() is rolled
    ctx = aws.get_switch_context()
    assert ctx.ready_event.is_set() and ctx.switching is False
    assert ctx.last_error == "docker daemon down"
    fns = [e[0] for e in service.events]
    assert fns == [
        "ui.sandbox.switching",
        "ui.sandbox.changed",
    ]  # changed = rolled-back state


@pytest.mark.asyncio
async def test_switch_lost_when_rollback_also_fails(wired, monkeypatch):
    service, calls = wired
    aws.set_sandbox_session(FakeSession("old"))
    aws.set_switch_context(_make_ctx())

    async def always_fail(**kw):
        raise RuntimeError("both gone")

    monkeypatch.setattr(serve_cli, "bring_up_session", always_fail)

    res = await serve_cli.perform_switch("/profiles/devbox.yml")
    assert res["ok"] is False and "rollback failed" in res["reason"]
    fns = [e[0] for e in service.events]
    assert fns == ["ui.sandbox.switching", "ui.sandbox.lost"]


@pytest.mark.asyncio
async def test_switch_no_context_returns_error(wired):
    aws.set_switch_context(None)
    res = await serve_cli.perform_switch("/profiles/devbox.yml")
    assert res["ok"] is False and "no live sandbox" in res["reason"]


# --------------------------------------------------------------------------
# observation tools
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_state_reports_context():
    from omni_code.tools.sandbox_tools import _state_dict

    aws.set_switch_context(_make_ctx(current_name="devbox"))
    aws.get_switch_context().backend = "docker"
    st = _state_dict()
    assert st["available"] is True and st["profile"] == "devbox"
    assert st["backend"] == "docker" and st["ready"] is True
    aws.set_switch_context(None)
    assert _state_dict()["available"] is False


@pytest.mark.asyncio
async def test_wait_until_ready_blocks_then_resolves():
    from omniagents.core.runtime.serve_state import get_switch_context

    ctx = _make_ctx()
    ctx.switching = True
    ctx.ready_event.clear()
    aws.set_switch_context(ctx)

    async def _finish_soon():
        await asyncio.sleep(0.05)
        ctx.switching = False
        ctx.ready_event.set()

    # The wait tool body awaits ready_event; flip it shortly after.
    asyncio.create_task(_finish_soon())
    deadline = asyncio.get_event_loop().time() + 2
    while not ctx.ready_event.is_set() and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.01)
    assert get_switch_context().ready_event.is_set()
    aws.set_switch_context(None)


def _coro(value):
    async def _c():
        return value

    return _c()
