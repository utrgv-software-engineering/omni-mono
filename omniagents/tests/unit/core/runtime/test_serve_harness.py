"""Tests for the serve-session harness (``omniagents.core.runtime.serve_harness``).

Covers the moved core with fakes — no docker daemon required:

* 3-tier docker resume (reused / rehydrated / fresh) plus the create
  fallthrough for non-docker or first-launch calls.
* snapshot scrub on a crafted tar containing symlink escapes.
* switch orchestration end-to-end through the ``set_switch_executor``
  seam, including the ``SwitchContext.profile_resolver`` product hook.
* teardown idempotence / ``None``-safety / snapshot discard.
"""

from __future__ import annotations

import asyncio
import io
import tarfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from omniagents.core.runtime import sandbox_lifecycle, serve_harness, serve_state
from omniagents.core.runtime.serve_harness import (
    _create_or_resume_session,
    _resolve_service_urls,
    _safe_symlink_target,
    _scrub_session_snapshot,
    perform_switch,
    teardown_session,
)
from omniagents.core.runtime.serve_state import SwitchContext


@pytest.fixture(autouse=True)
def _reset_serve_state():
    serve_state.reset()
    sandbox_lifecycle.set_switch_executor(None)
    yield
    serve_state.reset()
    sandbox_lifecycle.set_switch_executor(None)


# --------------------------------------------------------------------------
# Resume tiers
# --------------------------------------------------------------------------


class FakeDockerClient:
    """Records the state handed to ``resume``; optionally simulates the
    original container being gone (``resume`` mints a new container_id)."""

    def __init__(self, *, container_survives: bool):
        self.container_survives = container_survives
        self.resumed_state = None
        self.created = False

    async def resume(self, state):
        self.resumed_state = state
        if not self.container_survives:
            state.container_id = "ctr-replacement"
        return SimpleNamespace(state=state)

    async def create(self, *, manifest, options, snapshot):
        self.created = True
        return SimpleNamespace(state=SimpleNamespace(container_id=None))


def _docker_args(tmp_path: Path, *, with_tar: bool):
    from agents.sandbox.manifest import Manifest
    from agents.sandbox.snapshot import LocalSnapshot

    session_id = str(uuid.uuid4())
    snapshot = LocalSnapshot(id=session_id, base_path=tmp_path)
    if with_tar:
        with tarfile.open(tmp_path / f"{session_id}.tar", "w"):
            pass
    return {
        "client_type": "docker",
        "manifest": Manifest(),
        "options": SimpleNamespace(image="img:1", exposed_ports=[]),
        "snapshot": snapshot,
        "session_id": session_id,
        "prior_container_id": "ctr-old",
    }


def test_resume_tier_reused(tmp_path):
    client = FakeDockerClient(container_survives=True)
    session, tier = asyncio.run(
        _create_or_resume_session(
            client=client, **_docker_args(tmp_path, with_tar=True)
        )
    )
    assert tier == "reused"
    assert session.state.container_id == "ctr-old"
    assert client.resumed_state.container_id == "ctr-old"
    assert client.created is False


def test_resume_tier_rehydrated_when_container_gone_but_tar_exists(tmp_path):
    client = FakeDockerClient(container_survives=False)
    session, tier = asyncio.run(
        _create_or_resume_session(
            client=client, **_docker_args(tmp_path, with_tar=True)
        )
    )
    assert tier == "rehydrated"
    assert session.state.container_id == "ctr-replacement"


def test_resume_tier_fresh_when_container_and_tar_both_gone(tmp_path):
    client = FakeDockerClient(container_survives=False)
    session, tier = asyncio.run(
        _create_or_resume_session(
            client=client, **_docker_args(tmp_path, with_tar=False)
        )
    )
    assert tier == "fresh"


def test_resume_accepts_non_uuid_session_id(tmp_path):
    """Caller session ids are stable strings, not necessarily UUIDs — the
    launcher's resident agents pass slugs like ``resident-<id>``. Resume maps
    them through uuid5 (deterministic across restarts) instead of crashing on
    ``uuid.UUID(slug)``; real UUIDs keep round-tripping unchanged."""
    client = FakeDockerClient(container_survives=True)
    args = _docker_args(tmp_path, with_tar=True)
    args["session_id"] = "resident-product-engineer"
    session, tier = asyncio.run(_create_or_resume_session(client=client, **args))
    assert tier == "reused"
    expected = uuid.uuid5(uuid.NAMESPACE_URL, "omni-serve:resident-product-engineer")
    assert client.resumed_state.session_id == expected


def test_resume_falls_through_to_create_without_prior_container(tmp_path):
    client = FakeDockerClient(container_survives=True)
    args = _docker_args(tmp_path, with_tar=True)
    args["prior_container_id"] = None
    session, tier = asyncio.run(_create_or_resume_session(client=client, **args))
    assert tier is None
    assert client.created is True


def test_resume_falls_through_to_create_for_non_docker_backends(tmp_path):
    client = FakeDockerClient(container_survives=True)
    args = _docker_args(tmp_path, with_tar=True)
    args["client_type"] = "unix_local"
    session, tier = asyncio.run(_create_or_resume_session(client=client, **args))
    assert tier is None
    assert client.created is True


# --------------------------------------------------------------------------
# Snapshot scrub
# --------------------------------------------------------------------------


def test_safe_symlink_target_predicate():
    assert _safe_symlink_target("a/.fvm/flutter_sdk", "/Users/x/fvm/3.32.8") is False
    assert _safe_symlink_target("a/b/link", "../../../etc/passwd") is False
    assert _safe_symlink_target("a/b/link", "../b/file") is True
    assert _safe_symlink_target("a/link", "file") is True


def _add_file(tar, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _add_symlink(tar, name, target):
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    tar.addfile(info)


def test_scrub_drops_unsafe_symlinks_keeps_the_rest(tmp_path):
    snap = tmp_path / "sess.tar"
    with tarfile.open(snap, "w") as t:
        _add_file(t, "repo/README.md", b"hi")
        _add_symlink(t, "repo/.fvm/flutter_sdk", "/Users/someone/fvm/3.32.8")
        _add_symlink(t, "repo/escape", "../../../etc/passwd")
        _add_symlink(t, "repo/ok", "README.md")

    session = SimpleNamespace(
        state=SimpleNamespace(
            snapshot=SimpleNamespace(base_path=str(tmp_path), id="sess")
        )
    )
    _scrub_session_snapshot(session)

    with tarfile.open(snap, "r") as t:
        names = {m.name for m in t.getmembers()}
    assert names == {"repo/README.md", "repo/ok"}


def test_scrub_noops_on_noop_snapshot_and_missing_tar(tmp_path):
    # NoopSnapshot-like shape (no base_path/id) → silently skipped.
    _scrub_session_snapshot(SimpleNamespace(state=SimpleNamespace(snapshot=object())))
    # Configured snapshot whose tar was never written → silently skipped.
    session = SimpleNamespace(
        state=SimpleNamespace(
            snapshot=SimpleNamespace(base_path=str(tmp_path), id="never-persisted")
        )
    )
    _scrub_session_snapshot(session)


# --------------------------------------------------------------------------
# Service URL resolution
# --------------------------------------------------------------------------


class _PortSession:
    async def resolve_exposed_port(self, port):
        return SimpleNamespace(host="127.0.0.1", port=port)


def test_resolve_service_urls_applies_default_suffixes():
    from omniagents.core.sandbox.profiles import ServiceSpec

    urls = asyncio.run(
        _resolve_service_urls(
            _PortSession(),
            [
                (ServiceSpec(name="code_server", command="code-server", port=8080), 1),
                (ServiceSpec(name="vnc", command="start-vnc", port=6080), 2),
                (ServiceSpec(name="daemon", command="run-daemon", port=None), 3),
            ],
        )
    )
    assert urls == {
        "code_server": "http://127.0.0.1:8080",
        "vnc": "http://127.0.0.1:6080/vnc.html?autoconnect=true&resize=scale",
    }


def test_resolve_service_urls_honors_product_suffix_override():
    from omniagents.core.sandbox.profiles import ServiceSpec

    urls = asyncio.run(
        _resolve_service_urls(
            _PortSession(),
            [(ServiceSpec(name="vnc", command="start-vnc", port=6080), 1)],
            url_suffixes={},
        )
    )
    assert urls == {"vnc": "http://127.0.0.1:6080"}


# --------------------------------------------------------------------------
# Switch orchestration through the executor seam
# --------------------------------------------------------------------------


class FakeProfile:
    def __init__(self, backend="docker"):
        self.raw = {"client": {"type": backend}}
        self.terminal = object()


class FakeService:
    def __init__(self):
        self.events = []

    def active_run_ids(self):
        return []

    async def stop_run(self, run_id):  # pragma: no cover - no active runs
        return True

    async def request_client_function(
        self, function, args=None, *, session=None, expect_result=False, **kw
    ):
        self.events.append((function, args))
        return None


def _make_ctx(resolver):
    ctx = SwitchContext(
        app=object(),
        spec=object(),
        session_id="sess-1",
        snapshot_dir=None,
        sources_parsed=[],
        credentials_parsed=[],
        workspace_dir=Path("/tmp/ws"),
        project_id=None,
        host="127.0.0.1",
        port=3001,
        auth_token=None,
        profile_resolver=resolver,
        current_profile_path="/profiles/host.yml",
        current_profile_name="host",
        backend="unix_local",
        container_id=None,
        service_urls={},
        started_services=[("old", 1)],
    )
    ctx.ready_event.set()
    return ctx


def test_switch_executor_seam_end_to_end(monkeypatch, tmp_path):
    """Installing ``perform_switch`` via the seam and invoking it through
    ``switch_executor()`` swaps the published session, mutates the ctx in
    place, uses the product's ``profile_resolver``, and broadcasts
    switching → changed in order."""
    service = FakeService()
    monkeypatch.setattr(
        "omniagents.core.agents.service.current_service", lambda: service
    )

    resolver_calls = []

    def resolver(*, workspace_dir, project_id, explicit_path):
        resolver_calls.append(explicit_path)
        return FakeProfile(backend="docker")

    old_session = SimpleNamespace(name="old")
    new_session = SimpleNamespace(name="new")
    brought = SimpleNamespace(
        session=new_session,
        started_services=[("svc", 8080)],
        service_urls={"code_server": "http://x/ctr-new"},
        resume_tier="rehydrated",
        manifest=object(),
        client_type="docker",
        container_id="ctr-new",
    )

    teardowns = []

    async def fake_teardown(session, started_services):
        teardowns.append((session, started_services))

    async def fake_bring_up(**kw):
        assert kw["prior_container_id"] is None
        return brought

    monkeypatch.setattr(serve_harness, "teardown_session", fake_teardown)
    monkeypatch.setattr(serve_harness, "bring_up_session", fake_bring_up)
    monkeypatch.setattr(
        serve_harness, "_attach_session_to_spec", lambda spec, session, raw: None
    )
    monkeypatch.setattr(
        serve_harness,
        "_install_session_terminal_manager",
        lambda app, session, terminal: None,
    )

    serve_state.set_sandbox_session(old_session)
    serve_state.set_switch_context(_make_ctx(resolver))
    sandbox_lifecycle.set_switch_executor(perform_switch)

    executor = sandbox_lifecycle.switch_executor()
    assert executor is perform_switch

    res = asyncio.run(executor("/profiles/devbox.yml"))

    assert res["ok"] is True
    assert res["profile"] == "devbox"
    assert res["backend"] == "docker"
    assert res["container_id"] == "ctr-new"
    # The product resolver was consulted (framework fallback untouched).
    assert resolver_calls == [Path("/profiles/devbox.yml")]
    # Old session torn down; new one published.
    assert teardowns == [(old_session, [("old", 1)])]
    assert serve_state.get_sandbox_session() is new_session
    assert serve_state.get_orchestrator_spec() is not None
    ctx = serve_state.get_switch_context()
    assert ctx.current_profile_name == "devbox"
    assert ctx.container_id == "ctr-new"
    assert ctx.switching is False and ctx.ready_event.is_set()
    assert [e[0] for e in service.events] == [
        "ui.sandbox.switching",
        "ui.sandbox.changed",
    ]


def test_switch_without_context_reports_no_server():
    res = asyncio.run(perform_switch("/profiles/devbox.yml"))
    assert res["ok"] is False
    assert "no live sandbox" in res["reason"]


# --------------------------------------------------------------------------
# Teardown
# --------------------------------------------------------------------------


class TeardownSession:
    def __init__(self, *, stop_raises=False):
        self.calls = []
        self.stop_raises = stop_raises
        self.state = SimpleNamespace(snapshot=SimpleNamespace(id="snap-1"))

    async def pty_terminate_all(self):
        self.calls.append("pty_terminate_all")

    async def unpause(self):
        self.calls.append("unpause")

    async def stop(self):
        self.calls.append("stop")
        if self.stop_raises:
            raise RuntimeError("backend gone")

    async def aclose(self):
        self.calls.append("aclose")


def test_teardown_none_session_is_a_noop():
    asyncio.run(teardown_session(None, [("svc", 1)]))


def test_teardown_full_sequence_and_idempotence():
    session = TeardownSession()
    asyncio.run(teardown_session(session, [("svc", 1)]))
    assert session.calls == ["pty_terminate_all", "unpause", "stop", "aclose"]
    # Second teardown of the same session must not raise (idempotent from the
    # caller's perspective; backend errors are swallowed).
    asyncio.run(teardown_session(session, [("svc", 1)]))
    assert session.calls.count("stop") == 2


def test_teardown_skips_pty_terminate_without_started_services():
    session = TeardownSession()
    asyncio.run(teardown_session(session, []))
    assert session.calls == ["unpause", "stop", "aclose"]


def test_teardown_survives_stop_failure():
    session = TeardownSession(stop_raises=True)
    asyncio.run(teardown_session(session, []))
    assert "aclose" in session.calls  # still closed


def test_teardown_discard_snapshot_swaps_in_noop():
    from agents.sandbox.snapshot import NoopSnapshot

    session = TeardownSession()
    asyncio.run(teardown_session(session, [], discard_snapshot=True))
    assert isinstance(session.state.snapshot, NoopSnapshot)
    assert session.state.snapshot.id == "snap-1"
    assert "stop" in session.calls
