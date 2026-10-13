"""Cancellation-safe ownership of subprocesses created by HostWorkspace.

POSIX commands get their own process group; on Windows the process tree is
terminated with ``taskkill /T``. This contains ordinary shell children, not
descendants that deliberately escape via setsid or a remote job.
It is not an executor fence surviving death of the Python parent.
"""

import asyncio
import os
import signal
import subprocess


async def finish_cleanup(task):
    """Do not let repeated cancellation abandon an already-owned cleanup."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def await_cleanup(task):
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await finish_cleanup(task)
        raise


def signal_process(proc, *, force=False):
    if hasattr(proc, "signal_group"):
        proc.signal_group(force=force)
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL if force else signal.SIGTERM)
        elif proc.returncode is None:
            _kill_windows_tree(proc.pid, force=force)
    except ProcessLookupError:
        pass


def _kill_windows_tree(pid, *, force):
    """Windows has no process groups: a shell's children (a dev server the
    agent started under Git Bash) outlive ``terminate()`` of the shell.
    ``taskkill /T`` walks the tree; without ``/F`` it only asks politely,
    which console programs ignore, so the forced pass does the real work."""
    args = ["taskkill", "/PID", str(pid), "/T"] + (["/F"] if force else [])
    try:
        subprocess.run(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        pass


async def stop_process(proc, completion, *, grace=2.0):
    signal_process(proc)
    try:
        await asyncio.wait_for(asyncio.shield(completion), timeout=grace)
    except asyncio.TimeoutError:
        pass
    finally:
        # The shell may have exited while a child ignored SIGTERM or closed its
        # output pipes. Terminate the remainder of this owned group as well.
        signal_process(proc, force=True)
    try:
        return await asyncio.wait_for(asyncio.shield(completion), timeout=2.0)
    except asyncio.TimeoutError as exc:
        if hasattr(proc, "mark_uncertain"):
            proc.mark_uncertain()
        raise RuntimeError(
            "Command cleanup could not confirm completion; external outcome is unknown"
        ) from exc


async def create_process(factory, *args, **kwargs):
    task = asyncio.create_task(
        factory(*args, **kwargs, start_new_session=os.name == "posix")
    )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Cancellation may race fork/exec before asyncio publishes the Process.
        # Obtain the handle first, then clean it up instead of losing ownership.
        async def cleanup():
            proc = await task
            await stop_process(proc, asyncio.create_task(proc.communicate()))

        await finish_cleanup(asyncio.create_task(cleanup()))
        raise
