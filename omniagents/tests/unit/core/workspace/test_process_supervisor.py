"""Independent command retirement with an actually killed backend process."""

import asyncio
import json
import os
from pathlib import Path
import signal
import sys

import pytest

from omniagents.core.conversation.recorder import ConversationRecorder
from omniagents.core.conversation.store import SqliteConversationStore
from omniagents.core.runtime.ownership import current_ownership, reserve_run
from omniagents.workspace.host import HostWorkspace

pytestmark = [
    pytest.mark.skipif(sys.platform != "linux", reason="Linux supervisor"),
    pytest.mark.asyncio,
]


def running(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


async def eventually(predicate):
    async with asyncio.timeout(10):
        while not predicate():
            await asyncio.sleep(0.02)


def journals(root):
    return list((root / "scratch" / "executions").glob("*/state.json"))


async def test_supervisor_preserves_stdio_cwd_env_and_exit_status(tmp_path):
    workspace = HostWorkspace(str(tmp_path), scratch_root=str(tmp_path / "scratch"))
    handle = await workspace.spawn(
        [
            sys.executable,
            "-c",
            "import os,sys; print(os.getcwd()); print(os.getenv('TEST_VALUE')); print(sys.stdin.readline().strip()); print('err', file=sys.stderr); sys.exit(7)",
        ],
        shell=False,
        env={"TEST_VALUE": "private-value"},
    )
    await handle.write_stdin("input-value\n")
    result = await handle.wait(timeout=10)
    assert result.exit_code == 7
    assert result.stdout.splitlines() == [str(tmp_path), "private-value", "input-value"]
    assert result.stderr == "err\n"
    record = json.loads(journals(tmp_path)[0].read_text())
    assert record["state"] == "completed"
    assert handle.pid == record["command_pid"]
    assert handle.pid != record["supervisor_pid"]
    assert record["descendants_reaped"]
    assert "private-value" not in json.dumps(record)
    assert "input-value" not in json.dumps(record)
    assert not running(record["supervisor_pid"])


async def test_failed_exec_retains_os_error_and_not_started_evidence(tmp_path):
    workspace = HostWorkspace(str(tmp_path), scratch_root=str(tmp_path / "scratch"))
    with pytest.raises(FileNotFoundError):
        await workspace.exec([str(tmp_path / "missing-command")], shell=False)
    assert json.loads(journals(tmp_path)[0].read_text())["state"] == "not_started"


@pytest.mark.parametrize("mode", ["exec", "spawn", "leader_exited"])
async def test_backend_sigkill_stops_descendants_and_preserves_unknown_run(
    tmp_path, mode
):
    # A real separate backend owns both the durable reservation and lifetime FD.
    script = r"""
import asyncio, sys
from pathlib import Path
from omniagents.core.conversation.store import SqliteConversationStore
from omniagents.core.conversation.recorder import ConversationRecorder
from omniagents.core.runtime.ownership import current_ownership, reserve_run
from omniagents.workspace.host import HostWorkspace
async def main():
    root, mode = Path(sys.argv[1]), sys.argv[2]
    store = SqliteConversationStore(path=str(root / 'owner.db'))
    ConversationRecorder(store).ensure_thread('thread', project_legacy_history=False)
    current_ownership.set(reserve_run(store, 'thread'))
    workspace = HostWorkspace(str(root), scratch_root=str(root / 'scratch'))
    child = "import os,signal,sys,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(90)"
    parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); " + ("pass" if mode == 'leader_exited' else "time.sleep(90)")
    command = [sys.executable, '-c', parent, child, str(root / 'child.pid')]
    if mode == 'spawn':
        await workspace.spawn(command, shell=False)
        await asyncio.Event().wait()
    else:
        await workspace.exec(command, shell=False, timeout=None)
asyncio.run(main())
"""
    backend = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        str(tmp_path),
        mode,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    record = None
    try:
        await eventually(
            lambda: (tmp_path / "child.pid").exists()
            and (tmp_path / "child.pid").read_text()
        )
        child = int((tmp_path / "child.pid").read_text())
        journal = journals(tmp_path)[0]
        record = json.loads(journal.read_text())
        assert record["state"] == "running"
        assert record["run_owner"]["thread_id"] == "thread"
        assert running(child)
        backend.kill()
        await asyncio.wait_for(backend.communicate(), 5)
        await eventually(
            lambda: json.loads(journal.read_text())["state"] == "interrupted"
        )
        final = json.loads(journal.read_text())
        assert final["reason"] == "owner_disconnected"
        assert final["descendants_reaped"]
        await eventually(lambda: not running(final["supervisor_pid"]))
        assert not running(child)
        assert not running(final["command_pid"])
        # Cleanup evidence is NOT authority to repeat an operation with unknown
        # effects. A newly started runtime still cannot seize this session.
        store = SqliteConversationStore(path=str(tmp_path / "owner.db"))
        try:
            with pytest.raises(RuntimeError, match="unresolved runtime owner"):
                reserve_run(store, "thread")
        finally:
            store.close()
    finally:
        if backend.returncode is None:
            backend.kill()
        await backend.communicate()
        if record and running(record["command_pid"]):
            os.killpg(record["command_pid"], signal.SIGKILL)


@pytest.mark.parametrize(
    "evidence",
    [
        {"state": "cleanup_unconfirmed"},
        [],
        {"state": "completed", "returncode": 0},
        None,
    ],
)
async def test_unconfirmed_completion_keeps_owner_reserved(tmp_path, evidence):
    from omniagents.workspace._supervised_process import SupervisedProcess
    from types import SimpleNamespace

    store = SqliteConversationStore(path=str(tmp_path / "owner.db"))
    ConversationRecorder(store).ensure_thread("thread", project_legacy_history=False)
    owner = reserve_run(store, "thread")
    journal = tmp_path / "state.json"
    if evidence is not None:
        journal.write_text(json.dumps(evidence))
    # Test the adapter's fail-closed interpretation, without leaving an actual
    # uncontained process alive by killing its supervisor.
    process = object.__new__(SupervisedProcess)
    process._process = SimpleNamespace(returncode=0)
    process._result = None
    process._owner = owner
    process.journal = journal
    try:
        with pytest.raises(
            RuntimeError, match="outcome is unknown|evidence is unavailable"
        ):
            _ = process.returncode
        owner.release()
        assert owner.closing and owner.execution_uncertain and not owner.released
        with pytest.raises(RuntimeError, match="unresolved runtime owner"):
            reserve_run(store, "thread")
    finally:
        store.close()


async def test_owner_already_dead_prevents_dispatch(tmp_path):
    from omniagents.workspace import _supervisor

    journal = tmp_path / "state.json"
    _supervisor.save(journal, {"version": 1, "state": "starting"})
    read_end, write_end = os.pipe()
    ready_read, ready_write = os.pipe()
    os.close(write_end)
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-S",
            _supervisor.__file__,
            str(read_end),
            str(ready_write),
            str(journal),
            sys.executable,
            "-c",
            "from pathlib import Path; Path('must-not-exist').touch()",
            cwd=tmp_path,
            pass_fds=(read_end, ready_write),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(process.communicate(), 5)
        assert process.returncode == 0
        assert json.loads(journal.read_text())["state"] == "not_started"
        assert not (tmp_path / "must-not-exist").exists()
    finally:
        for fd in (read_end, ready_read, ready_write):
            os.close(fd)


async def test_cancellation_during_supervisor_publication_retires_command(
    tmp_path, monkeypatch
):
    from omniagents.workspace import _supervised_process

    entered, publish = asyncio.Event(), asyncio.Event()
    original = _supervised_process.supervised_process

    async def delayed(*args, **kwargs):
        process = await original(*args, **kwargs)
        entered.set()
        await publish.wait()
        return process

    monkeypatch.setattr(_supervised_process, "supervised_process", delayed)
    workspace = HostWorkspace(str(tmp_path), scratch_root=str(tmp_path / "scratch"))
    task = asyncio.create_task(
        workspace.exec(
            [sys.executable, "-c", "import time; time.sleep(90)"], shell=False
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        publish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        record = json.loads(journals(tmp_path)[0].read_text())
        assert record["state"] == "interrupted"
        assert record["descendants_reaped"]
        assert not running(record["command_pid"])
    finally:
        publish.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_detached_survivor_is_unknown_and_blocks_run_release(tmp_path):
    store = SqliteConversationStore(path=str(tmp_path / "owner.db"))
    ConversationRecorder(store).ensure_thread("thread", project_legacy_history=False)
    owner = reserve_run(store, "thread")
    token = current_ownership.set(owner)
    workspace = HostWorkspace(str(tmp_path), scratch_root=str(tmp_path / "scratch"))
    child = "import os,sys,time; from pathlib import Path; Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(90)"
    parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); time.sleep(90)"
    task = asyncio.create_task(
        workspace.exec(
            [sys.executable, "-c", parent, child, str(tmp_path / "detached.pid")],
            shell=False,
            timeout=None,
        )
    )
    detached_pid = None
    try:
        await eventually(
            lambda: (tmp_path / "detached.pid").exists()
            and (tmp_path / "detached.pid").read_text()
        )
        detached_pid = int((tmp_path / "detached.pid").read_text())
        task.cancel()
        with pytest.raises(RuntimeError, match="outcome is unknown"):
            await asyncio.wait_for(task, 8)
        assert running(detached_pid)
        owner.release()
        assert owner.execution_uncertain and not owner.released
        with pytest.raises(RuntimeError, match="unresolved runtime owner"):
            reserve_run(store, "thread")
        await eventually(
            lambda: json.loads(journals(tmp_path)[0].read_text())["state"]
            == "cleanup_unconfirmed"
        )
    finally:
        if detached_pid is not None and running(detached_pid):
            # This test's 90-second child is still alive throughout the bounded
            # cancellation check. Verify its unique temp-path argument as well.
            command_line = Path(f"/proc/{detached_pid}/cmdline").read_bytes()
            assert str(tmp_path / "detached.pid").encode() in command_line.split(b"\0")
            os.kill(detached_pid, signal.SIGKILL)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        current_ownership.reset(token)
        store.close()
