"""``stream_agent`` wires the run-diff observer for the run it drives.

The aggregation logic is unit-tested in
``tests/unit/core/conversation/test_run_diff.py``; what these tests defend
is the *wiring*: a real ``stream_agent`` call must bind the observer before
the agent runs, persist the aggregate as a ``run_diff`` item under the run's
turn, push ``item_updated``, and unbind on the way out.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from omniagents import workspace as ws
import omniagents.core.runtime.bridge as bridge
from omniagents.core.conversation.model import ItemKind
from omniagents.core.conversation.store import (
    get_conversation_store,
    reset_conversation_store,
)
from omniagents.execution import (
    EnvironmentKind,
    EnvironmentLease,
    EnvironmentState,
    ExecutionEnvironmentDescriptor,
)
from omniagents.workspace.host import HostWorkspace

RUN_ID = "run-diff-1"


class DummyChannel:
    def __init__(self) -> None:
        self.calls = []

    async def async_call(self, method, args):
        self.calls.append((method, args))


class DummySession:
    def __init__(self) -> None:
        self.channels = []
        self.history = []
        self.pending_events = []
        self.id = "ws-session"
        self.user_id = None
        self.archived = False

    def append_message(self, message):
        self.history.append(message)


class DummyService:
    """Just enough of AgentService for the bridge's publish path."""

    def __init__(self) -> None:
        self.updates = []

    async def notify_item_updated(self, session, item):
        self.updates.append((session.id, item))


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIAGENTS_HISTORY_DB", str(tmp_path / "sessions.db"))
    reset_conversation_store()
    yield
    reset_conversation_store()


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return root


def _fake_sdk(monkeypatch, during_run):
    monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
    monkeypatch.setattr(
        bridge,
        "ItemHelpers",
        types.SimpleNamespace(text_message_output=lambda item: ""),
    )

    class FakeRunResult:
        async def stream_events(self):
            await during_run()
            if False:  # pragma: no cover - makes this an async generator
                yield None

    class FakeRunner:
        @classmethod
        def run_streamed(cls, *args, **kwargs):
            return FakeRunResult()

    class FakeRunConfig:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("agents.Runner", FakeRunner)
    monkeypatch.setattr("agents.RunConfig", FakeRunConfig)


def _agent():
    return types.SimpleNamespace(
        name="Agent",
        instructions="",
        tools=[],
        model="model-x",
        model_settings=types.SimpleNamespace(max_tokens=None, truncation=None),
    )


_DEFAULT = object()


async def _drive(monkeypatch, workspace, during_run, service=_DEFAULT, session=None):
    _fake_sdk(monkeypatch, during_run)
    session = session or DummySession()
    service = DummyService() if service is _DEFAULT else service
    task = asyncio.current_task()
    previous_name = task.get_name()
    task.set_name(RUN_ID)
    environment_lease = EnvironmentLease(
        ExecutionEnvironmentDescriptor(
            environment_id="run-diff-environment",
            workspace_id="run-diff-workspace",
            kind=EnvironmentKind.HOST,
            state=EnvironmentState.READY,
            generation=1,
        ),
        HostWorkspace(str(workspace)),
    )
    try:
        await bridge.stream_agent(
            _agent(),
            "prompt",
            session,
            rpc_channel=DummyChannel(),
            service=service,
            max_turns=1,
            context={"workspace_root": str(workspace)},
            environment_lease=environment_lease,
        )
    finally:
        task.set_name(previous_name)
    return service, session


@pytest.mark.asyncio
async def test_run_records_a_run_diff_item_under_its_turn(
    monkeypatch, workspace, isolated_store
):
    async def during_run():
        await ws.write_text("a.txt", "first\n")

    service, session = await _drive(monkeypatch, workspace, during_run)

    rows = get_conversation_store(None, None).list_items(
        session.id, limit=10, kinds=[ItemKind.RUN_DIFF]
    )
    assert len(rows) == 1
    assert rows[0].turn_id == RUN_ID
    assert rows[0].content["files"][0]["path"] == "a.txt"
    assert "+first" in rows[0].content["diff"]


@pytest.mark.asyncio
async def test_repeated_edits_to_one_file_aggregate_to_the_net_diff(
    monkeypatch, workspace, isolated_store
):
    (workspace / "a.txt").write_bytes("v0\n".encode())

    async def during_run():
        await ws.write_text("a.txt", "v1\n")
        await ws.write_text("a.txt", "v2\n")
        await ws.write_text("a.txt", "v3\n")

    service, session = await _drive(monkeypatch, workspace, during_run)

    rows = get_conversation_store(None, None).list_items(
        session.id, limit=10, kinds=[ItemKind.RUN_DIFF]
    )
    assert len(rows) == 1
    diff = rows[0].content["diff"]
    assert rows[0].content["stats"]["files_changed"] == 1
    assert "-v0" in diff and "+v3" in diff
    assert "v1" not in diff and "v2" not in diff


@pytest.mark.asyncio
async def test_create_then_delete_leaves_an_empty_aggregate(
    monkeypatch, workspace, isolated_store
):
    async def during_run():
        await ws.write_text("scratch.txt", "temp\n")
        await ws.remove("scratch.txt")

    service, session = await _drive(monkeypatch, workspace, during_run)

    rows = get_conversation_store(None, None).list_items(
        session.id, limit=10, kinds=[ItemKind.RUN_DIFF]
    )
    assert len(rows) == 1
    assert rows[0].content["files"] == []
    assert rows[0].content["diff"] == ""


@pytest.mark.asyncio
async def test_every_edit_pushes_item_updated(monkeypatch, workspace, isolated_store):
    async def during_run():
        await ws.write_text("a.txt", "one\n")
        await ws.write_text("b.txt", "two\n")

    service, session = await _drive(monkeypatch, workspace, during_run)

    # Two per-edit pushes, plus the settling re-capture at the end of the run.
    assert len(service.updates) == 3
    assert {item.kind for _, item in service.updates} == {ItemKind.RUN_DIFF}
    items = [item for _, item in service.updates]
    assert len({item.item_id for item in items}) == 1
    assert [item.revision for item in items] == sorted(item.revision for item in items)


@pytest.mark.asyncio
async def test_a_run_that_changes_nothing_records_no_run_diff(
    monkeypatch, workspace, isolated_store
):
    async def during_run():
        return None

    service, session = await _drive(monkeypatch, workspace, during_run)
    assert service.updates == []
    store = get_conversation_store(None, None)
    assert store.list_items(session.id, limit=10, kinds=[ItemKind.RUN_DIFF]) == []


@pytest.mark.asyncio
async def test_an_edit_with_no_net_effect_records_no_run_diff(
    monkeypatch, workspace, isolated_store
):
    """A real mutation that leaves the tree byte-identical must not mint an
    item. Distinct from the case above: here the observer DOES fire, and it
    is the empty-aggregate gate that has to hold."""
    (workspace / "a.txt").write_bytes("same\n".encode())

    async def during_run():
        await ws.write_text("a.txt", "same\n")

    service, session = await _drive(monkeypatch, workspace, during_run)
    assert service.updates == []
    store = get_conversation_store(None, None)
    assert store.list_items(session.id, limit=10, kinds=[ItemKind.RUN_DIFF]) == []


@pytest.mark.asyncio
async def test_a_diff_that_returns_to_empty_is_still_pushed(
    monkeypatch, workspace, isolated_store
):
    """Once an item exists it keeps being updated, including back down to
    empty — a diff that went to zero is a fact the client must see."""

    async def during_run():
        await ws.write_text("tmp.txt", "scratch\n")
        await ws.remove("tmp.txt")

    service, session = await _drive(monkeypatch, workspace, during_run)
    assert len(service.updates) == 3  # create, remove, settle
    assert service.updates[0][1].content["stats"]["files_changed"] == 1
    assert service.updates[-1][1].content["files"] == []


@pytest.mark.asyncio
async def test_a_shell_rewrite_after_an_api_write_is_reconciled(
    monkeypatch, workspace, isolated_store
):
    """The aggregate must not ASSERT content that is not on disk.

    The observer only fires on the workspace content primitives, so a file
    written through ``write_file`` and then rewritten by a shell command
    (a formatter, ``git checkout``, ``sed -i``) kept its API-time
    post-image. The settled diff then claimed bytes the workspace does not
    hold. The end-of-run re-capture reconciles it.
    """

    async def during_run():
        await ws.write_text("mod.py", "def f( ):\n    pass\n")
        # Stand-in for ``execute_bash`` running a formatter: the bytes change
        # without going through omniagents.workspace at all.
        (workspace / "mod.py").write_bytes("def f():\n    pass\n".encode())

    service, session = await _drive(monkeypatch, workspace, during_run)

    rows = get_conversation_store(None, None).list_items(
        session.id, limit=10, kinds=[ItemKind.RUN_DIFF]
    )
    diff = rows[0].content["diff"]
    assert "+def f():" in diff
    assert "def f( )" not in diff
    assert rows[0].content["files"][0]["path"] == "mod.py"


@pytest.mark.asyncio
async def test_a_shell_revert_after_an_api_write_cancels_the_change(
    monkeypatch, workspace, isolated_store
):
    """The same reconciliation the other way: if the shell put the file back
    the way it was, the run changed nothing and must say so."""
    (workspace / "mod.py").write_bytes("original\n".encode())

    async def during_run():
        await ws.write_text("mod.py", "edited\n")
        (workspace / "mod.py").write_bytes("original\n".encode())

    service, session = await _drive(monkeypatch, workspace, during_run)

    rows = get_conversation_store(None, None).list_items(
        session.id, limit=10, kinds=[ItemKind.RUN_DIFF]
    )
    assert rows[0].content["files"] == []
    assert rows[0].content["diff"] == ""


@pytest.mark.asyncio
async def test_a_shell_deletion_after_an_api_write_is_reconciled(
    monkeypatch, workspace, isolated_store
):
    async def during_run():
        await ws.write_text("tmp.txt", "scratch\n")
        (workspace / "tmp.txt").unlink()

    service, session = await _drive(monkeypatch, workspace, during_run)

    rows = get_conversation_store(None, None).list_items(
        session.id, limit=10, kinds=[ItemKind.RUN_DIFF]
    )
    assert rows[0].content["files"] == []


@pytest.mark.asyncio
async def test_observer_is_unbound_after_the_run(
    monkeypatch, workspace, isolated_store
):
    """A leaked observer would attribute the NEXT run's edits to this one."""

    async def during_run():
        assert ws.current_mutation_observer() is not None

    await _drive(monkeypatch, workspace, during_run)
    assert ws.current_mutation_observer() is None


@pytest.mark.asyncio
async def test_no_observer_without_a_service(monkeypatch, workspace, isolated_store):
    """Nothing to publish to means nothing to attribute; stay out of the way."""
    seen = {}

    async def during_run():
        seen["observer"] = ws.current_mutation_observer()
        await ws.write_text("a.txt", "x\n")

    await _drive(monkeypatch, workspace, during_run, service=None)
    assert seen["observer"] is None


# ---------------------------------------------------------------------
# The teardown must never replace the run's own outcome
# ---------------------------------------------------------------------


class _CancellingObserver:
    """Stands in for an observer whose per-path re-reads are interrupted.

    ``finalize()`` awaits once per tracked path, so a ``task.cancel()``
    landing anywhere in that window surfaces as a CancelledError — which
    ``except Exception`` does not catch.
    """

    def __init__(self) -> None:
        self.finalized = False

    async def finalize(self) -> None:
        self.finalized = True
        raise asyncio.CancelledError()


@pytest.mark.asyncio
async def test_teardown_does_not_replace_the_runs_exception(monkeypatch):
    """A cancellation during finalize() must not mask the real failure.

    Left uncaught, the CancelledError escapes `stream_agent`'s `finally` and
    REPLACES the run's exception — `_worker` then records
    ``end_reason="cancelled"`` and drops ``run_end.error``, so a run that
    died with a RuntimeError is filed as a user cancellation.
    """
    observer = _CancellingObserver()
    monkeypatch.setattr(
        bridge, "_install_run_diff_observer", lambda *a: (object(), observer)
    )
    monkeypatch.setattr(
        bridge, "reset_mutation_observer", lambda token: None, raising=False
    )

    with pytest.raises(RuntimeError, match="the real failure"):
        async with bridge.run_diff_observer_scope(object(), object()):
            raise RuntimeError("the real failure")
    assert observer.finalized


@pytest.mark.asyncio
async def test_teardown_still_surfaces_a_genuine_cancellation(monkeypatch):
    """The mirror case: with nothing in flight, swallowing the
    CancelledError would LOSE a real cancellation instead of masking one."""
    observer = _CancellingObserver()
    monkeypatch.setattr(
        bridge, "_install_run_diff_observer", lambda *a: (object(), observer)
    )

    with pytest.raises(asyncio.CancelledError):
        async with bridge.run_diff_observer_scope(object(), object()):
            pass


@pytest.mark.asyncio
async def test_an_ordinary_finalize_fault_never_escapes(monkeypatch):
    class Exploding:
        async def finalize(self):
            raise OSError("workspace gone")

    monkeypatch.setattr(
        bridge, "_install_run_diff_observer", lambda *a: (object(), Exploding())
    )
    async with bridge.run_diff_observer_scope(object(), object()):
        pass


@pytest.mark.asyncio
async def test_the_scope_is_a_no_op_without_a_run(monkeypatch):
    monkeypatch.setattr(bridge, "_install_run_diff_observer", lambda *a: None)
    async with bridge.run_diff_observer_scope(object(), object()) as observer:
        assert observer is None


# ---------------------------------------------------------------------
# The cancelled-tool_result broadcast is in the SAME teardown
# ---------------------------------------------------------------------


class _ToolCallItem:
    """Minimal stand-in for the SDK's ``ToolCallItem``."""

    type = "tool_call_item"

    def to_input_item(self):
        return {"name": "edit_file", "call_id": "call-1", "arguments": "{}"}


class _RunItemEvent:
    type = "run_item_stream_event"

    def __init__(self, item):
        self.item = item


def _fake_sdk_streaming(monkeypatch, events, *, raise_at_end=None):
    """A fake SDK run that yields *events* and then optionally raises."""
    monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
    monkeypatch.setattr(
        bridge,
        "ItemHelpers",
        types.SimpleNamespace(text_message_output=lambda item: ""),
    )

    class FakeRunResult:
        async def stream_events(self):
            for event in events:
                yield event
            if raise_at_end is not None:
                raise raise_at_end

    class FakeRunner:
        @classmethod
        def run_streamed(cls, *args, **kwargs):
            return FakeRunResult()

    class FakeRunConfig:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("agents.Runner", FakeRunner)
    monkeypatch.setattr("agents.RunConfig", FakeRunConfig)


def _break_cancelled_broadcast(monkeypatch, exc):
    """Make ONLY the cancelled-tool_result broadcast fail."""
    real = bridge._broadcast

    async def broadcast(session, method, payload):
        if payload.get("output") == "[Cancelled by user]":
            raise exc
        return await real(session, method, payload)

    monkeypatch.setattr(bridge, "_broadcast", broadcast)


async def _drive_with_pending_tool_call(monkeypatch, workspace, **kwargs):
    _fake_sdk_streaming(monkeypatch, [_RunItemEvent(_ToolCallItem())], **kwargs)
    task = asyncio.current_task()
    previous = task.get_name()
    task.set_name(RUN_ID)
    try:
        await bridge.stream_agent(
            _agent(),
            "prompt",
            DummySession(),
            rpc_channel=DummyChannel(),
            service=DummyService(),
            max_turns=1,
            context={"workspace_root": str(workspace)},
        )
    finally:
        task.set_name(previous)


@pytest.mark.asyncio
async def test_a_cancel_in_the_pending_tool_broadcast_keeps_the_runs_exception(
    monkeypatch, workspace, isolated_store
):
    """The pending-tool teardown awaits in the same ``finally`` the run-diff
    scope does, so it has the same exposure. ``except Exception`` covers a
    transport fault but NOT a ``CancelledError`` delivered during the send —
    that escaped and rewrote a RuntimeError run as a cancellation."""
    _break_cancelled_broadcast(monkeypatch, asyncio.CancelledError())

    with pytest.raises(RuntimeError, match="the real failure"):
        await _drive_with_pending_tool_call(
            monkeypatch, workspace, raise_at_end=RuntimeError("the real failure")
        )


@pytest.mark.asyncio
async def test_a_cancel_in_the_pending_tool_broadcast_still_surfaces_alone(
    monkeypatch, workspace, isolated_store
):
    """Mirror case: nothing in flight, so swallowing would LOSE a genuine
    cancellation rather than mask one."""
    _break_cancelled_broadcast(monkeypatch, asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await _drive_with_pending_tool_call(monkeypatch, workspace)


@pytest.mark.asyncio
async def test_a_transport_fault_in_the_pending_tool_broadcast_never_escapes(
    monkeypatch, workspace, isolated_store
):
    _break_cancelled_broadcast(monkeypatch, OSError("socket gone"))
    await _drive_with_pending_tool_call(monkeypatch, workspace)
