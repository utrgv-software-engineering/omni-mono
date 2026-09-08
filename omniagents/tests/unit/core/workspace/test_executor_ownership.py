"""Real process and delayed-I/O regressions for run retirement."""

import asyncio
import os
from pathlib import Path
import signal
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from omniagents.core.conversation.recorder import ConversationRecorder
from omniagents.core.conversation.store import SqliteConversationStore
from omniagents.core.runtime.ownership import current_ownership, reserve_run
from omniagents.workspace.host import HostWorkspace
from omniagents.workspace.sandbox import SandboxWorkspace


@pytest.fixture
def owner(tmp_path):
    store = SqliteConversationStore(path=str(tmp_path / "owner.db"))
    ConversationRecorder(store).ensure_thread("thread", project_legacy_history=False)
    ownership = reserve_run(store, "thread")
    token = current_ownership.set(ownership)
    try:
        yield ownership
    finally:
        ownership.release()
        current_ownership.reset(token)
        store.close()


@pytest.mark.asyncio
async def test_cached_workspace_cannot_write_or_execute_after_run_closes(
    tmp_path, owner
):
    workspace = HostWorkspace(str(tmp_path))
    owner.release()
    for operation in (
        workspace.write_text("late.txt", "must not exist"),
        workspace.mkdir("late-dir"),
        workspace.exec([sys.executable, "-c", "raise SystemExit(0)"], shell=False),
        workspace.spawn([sys.executable, "-c", "raise SystemExit(0)"], shell=False),
    ):
        with pytest.raises(RuntimeError, match="no longer current"):
            await operation
    assert not (tmp_path / "late.txt").exists()
    assert not (tmp_path / "late-dir").exists()


@pytest.mark.asyncio
async def test_stale_sandbox_workspace_is_rejected_before_remote_dispatch(owner):
    remote = SimpleNamespace(exec=AsyncMock())
    workspace = SandboxWorkspace(remote, root="/workspace")
    owner.release()
    with pytest.raises(RuntimeError, match="no longer current"):
        await workspace.exec("must not reach remote executor")
    remote.exec.assert_not_called()


@pytest.mark.asyncio
async def test_stale_task_cannot_send_commands_to_a_background_process(tmp_path, owner):
    workspace = HostWorkspace(str(tmp_path))
    handle = await workspace.spawn(
        [sys.executable, "-u", "-c", "import sys; print(sys.stdin.readline())"],
        shell=False,
    )
    replacement = None
    try:
        owner.release()
        with pytest.raises(RuntimeError, match="no longer current"):
            await handle.write_stdin("stale owner\n")
        replacement = reserve_run(owner.store, "thread")
        token = current_ownership.set(replacement)
        try:
            await handle.write_stdin("current owner\n")
            result = await handle.wait(timeout=5)
            assert result.stdout.strip() == "current owner"
        finally:
            current_ownership.reset(token)
    finally:
        await handle.terminate(force_after=0.1)
        if replacement:
            replacement.release()


@pytest.mark.asyncio
async def test_retirement_waits_for_admitted_write_and_rejects_late_writes(
    tmp_path, owner
):
    entered, finish = asyncio.Event(), asyncio.Event()

    class DelayedWorkspace(HostWorkspace):
        async def write_text(self, path, content, **kwargs):
            entered.set()
            await finish.wait()
            # Simulates an already-admitted executor write completing, not a
            # new Workspace operation after closure.
            Path(self.root, path).write_text(content)

    workspace = DelayedWorkspace(str(tmp_path))
    task = asyncio.create_task(workspace.write_text("admitted", "saved"))
    await entered.wait()
    task.cancel()
    owner.release()
    assert owner.closing and not owner.released
    with pytest.raises(RuntimeError, match="unresolved runtime owner"):
        reserve_run(owner.store, "thread")
    with pytest.raises(RuntimeError, match="no longer current"):
        await workspace.write_text("late", "no")
    task.cancel()  # Repeated cancellation must not abandon the admitted write.
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (tmp_path / "admitted").read_text() == "saved"
    assert not (tmp_path / "late").exists()
    assert owner.released
    reserve_run(owner.store, "thread").release()


def running(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


@pytest.mark.skipif(
    sys.platform != "linux", reason="Uses /proc to verify process death"
)
@pytest.mark.asyncio
async def test_cancellation_while_process_handle_is_being_published(tmp_path):
    from omniagents.workspace._process import create_process

    entered, publish = asyncio.Event(), asyncio.Event()
    processes = []

    async def delayed_factory(*args, **kwargs):
        proc = await asyncio.create_subprocess_exec(*args, **kwargs)
        processes.append(proc)
        entered.set()
        await publish.wait()
        return proc

    task = asyncio.create_task(
        create_process(
            delayed_factory,
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    )
    try:
        await entered.wait()
        task.cancel()
        publish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        assert not running(processes[0].pid)
    finally:
        publish.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.skipif(sys.platform != "linux", reason="Uses POSIX pipe inheritance")
@pytest.mark.asyncio
async def test_background_wait_timeout_includes_output_drain(tmp_path):
    workspace = HostWorkspace(str(tmp_path))
    # The leader exits immediately; its child still owns the inherited pipes.
    command = [
        sys.executable,
        "-c",
        "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])",
    ]
    handle = await workspace.spawn(command, shell=False)
    try:
        result = await asyncio.wait_for(handle.wait(timeout=0.05), timeout=1)
        assert result.exit_code == -1
        assert await handle.status() == "running"
    finally:
        await handle.terminate(force_after=0.1)
        await asyncio.wait_for(handle.wait(), timeout=3)


@pytest.mark.skipif(sys.platform != "linux", reason="Uses /proc to verify child death")
@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "timeout", "background"])
async def test_host_stop_terminates_shell_children_that_ignore_sigterm(tmp_path, stop):
    workspace = HostWorkspace(str(tmp_path))
    pidfile = tmp_path / "child.pid"
    parentfile = tmp_path / "parent.pid"
    child = "import os,signal,sys,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
    parent = "import os,subprocess,sys,time; from pathlib import Path; Path(sys.argv[1]).write_text(str(os.getpid())); subprocess.Popen([sys.executable,'-c',sys.argv[2],sys.argv[3]]); time.sleep(30)"
    command = [sys.executable, "-c", parent, str(parentfile), child, str(pidfile)]
    handle = None
    task = None
    try:
        if stop == "background":
            handle = await workspace.spawn(command, shell=False)
        else:
            task = asyncio.create_task(
                workspace.exec(
                    command, shell=False, timeout=0.5 if stop == "timeout" else 30
                )
            )
        async with asyncio.timeout(5):
            while not pidfile.exists():
                await asyncio.sleep(0.01)
        child_pid = int(pidfile.read_text())
        assert running(child_pid)
        if handle:
            await handle.terminate(force_after=0.1)
            await asyncio.wait_for(handle.wait(), timeout=3)
        elif stop == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)
        else:
            result = await asyncio.wait_for(task, timeout=5)
            assert result.exit_code == -1
        async with asyncio.timeout(3):
            while running(child_pid):
                await asyncio.sleep(0.01)
    finally:
        if parentfile.exists():
            try:
                os.killpg(int(parentfile.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
