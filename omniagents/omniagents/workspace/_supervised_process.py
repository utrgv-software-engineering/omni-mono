"""Async adapter for the independent Linux supervisor.

Journals are evidence, not takeover permission. In particular, 'interrupted'
does not mean an operation had no effects. Nothing here releases a run owner.
"""

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

from ._process import create_process
from ._supervisor import save

_watchers = set()


class SupervisedProcess:
    def __init__(self, process, control, journal, owner):
        self._process = process
        self._control = control
        self.journal = journal
        self.supervisor_pid = process.pid
        self.pid = json.loads(journal.read_text())["command_pid"]
        self.stdin = process.stdin
        self.stdout = process.stdout
        self.stderr = process.stderr
        self._result = None
        self._owner = owner
        watcher = asyncio.create_task(self._close_when_finished())
        _watchers.add(watcher)
        watcher.add_done_callback(_watchers.discard)

    async def _close_when_finished(self):
        try:
            await self._process.wait()
        finally:
            if self._control is not None:
                os.close(self._control)
                self._control = None

    def signal_group(self, *, force=False):
        if self._control is not None:
            try:
                os.write(self._control, b"K" if force else b"T")
            except BrokenPipeError:
                pass

    @property
    def returncode(self):
        if self._process.returncode is None:
            return None
        if self._result is None:
            try:
                self._result = json.loads(self.journal.read_text())
            except (OSError, ValueError):
                self.mark_uncertain()
                raise RuntimeError(
                    "Command completion evidence is unavailable"
                ) from None
        if (
            not isinstance(self._result, dict)
            or self._result.get("state") not in ("completed", "interrupted")
            or self._result.get("descendants_reaped") is not True
            or type(self._result.get("returncode")) is not int
        ):
            self.mark_uncertain()
            raise RuntimeError(
                "Command supervisor could not confirm cleanup; outcome is unknown"
            )
        return self._result["returncode"]

    def mark_uncertain(self):
        if self._owner is not None and not self._owner.released:
            self._owner.execution_uncertain = True
            self._owner.closing = True

    async def wait(self):
        await self._process.wait()
        return self.returncode

    async def communicate(self, *args, **kwargs):
        output = await self._process.communicate(*args, **kwargs)
        await self.wait()
        return output


async def supervised_process(command, *, scratch_root, **kwargs):
    """Publish only after supervision is installed; outer create_process shields it."""
    from omniagents.core.runtime.ownership import current_ownership

    owner = current_ownership.get()
    record = {"version": 1, "execution_id": uuid.uuid4().hex, "state": "starting"}
    if owner:
        record["run_owner"] = {
            key: getattr(owner, key)
            for key in ("thread_id", "run_id", "owner", "generation")
        }

    def prepare():
        directory = Path(scratch_root) / "executions"
        directory.mkdir(parents=True, exist_ok=True)
        journal = Path(tempfile.mkdtemp(prefix="exec-", dir=directory)) / "state.json"
        save(journal, record)
        return journal

    journal = await asyncio.to_thread(prepare)
    control_read = control_write = ready_read = ready_write = None
    process = None
    adapter = None
    try:
        control_read, control_write = os.pipe()
        ready_read, ready_write = os.pipe()
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-S",
            str(Path(__file__).with_name("_supervisor.py")),
            str(control_read),
            str(ready_write),
            str(journal),
            *command,
            pass_fds=(control_read, ready_write),
            **kwargs,
        )
        os.close(control_read)
        control_read = None
        os.close(ready_write)
        ready_write = None
        reader = asyncio.StreamReader()
        pipe = os.fdopen(ready_read, "rb")
        ready_read = None
        try:
            transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(reader), pipe
            )
        except BaseException:
            pipe.close()
            raise
        try:
            ready = await reader.readline()
        finally:
            transport.close()
        if ready != b"ready\n":
            os.close(control_write)
            control_write = None
            await asyncio.wait_for(process.communicate(), 3)
            try:
                result = json.loads(journal.read_text())
            except (OSError, ValueError):
                result = {}
            if not isinstance(result, dict):
                result = {}
            if result.get("state") != "not_started" and owner is not None:
                owner.execution_uncertain = True
                owner.closing = True
            if result.get("errno"):
                raise OSError(result["errno"], "Command could not start", command[0])
            raise RuntimeError("Command supervisor did not confirm dispatch")
        adapter = SupervisedProcess(process, control_write, journal, owner)
        control_write = None
        return adapter
    finally:
        for fd in (control_read, control_write, ready_read, ready_write):
            if fd is not None:
                os.close(fd)
        if process is not None and adapter is None:
            # Any adapter/startup failure after launch must retain ownership
            # unless the supervisor durably proved it never dispatched.
            try:
                state = json.loads(journal.read_text())
            except (OSError, ValueError):
                state = None
            if owner is not None and (
                not isinstance(state, dict) or state.get("state") != "not_started"
            ):
                owner.execution_uncertain = True
                owner.closing = True
            await asyncio.wait_for(process.communicate(), 3)


async def create_host_process(factory, *args, scratch_root, **kwargs):
    if sys.platform != "linux":
        return await create_process(factory, *args, **kwargs)
    command = (
        ["/bin/sh", "-c", args[0]]
        if factory is asyncio.create_subprocess_shell
        else list(args)
    )
    return await create_process(
        supervised_process, command, scratch_root=scratch_root, **kwargs
    )
