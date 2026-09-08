"""Linux command supervisor, invoked as an isolated stdlib-only script.

The backend owns the sole write end of a lifetime pipe. EOF means its command
group must stop, even if the backend was SIGKILLed. A subreaper waits for ordinary
descendants, not just the shell. This is not a sandbox or a remote-effect fence.
"""

import ctypes
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time


def save(path, record):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(record, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def supervise(control, ready, path, command):
    record = json.loads(path.read_text())
    child = None
    finished = False
    announced = False
    stop_requested = False

    def request_stop(signum, frame):
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    # Neither the command nor its descendants may inherit the lifetime pipe.
    os.set_inheritable(control, False)
    os.set_inheritable(ready, False)
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise OSError(ctypes.get_errno(), "Cannot establish command subreaper")
        # Refuse dispatch when the owner died before this supervisor started.
        if select.select([control], [], [], 0)[0]:
            record.update(state="not_started", reason="owner_closed_before_dispatch")
            save(path, record)
            return
        record.update(state="launching", supervisor_pid=os.getpid())
        save(path, record)
        child = subprocess.Popen(command, start_new_session=True)
        record.update(state="running", command_pid=child.pid)
        save(path, record)
        os.write(ready, b"ready\n")
        os.close(ready)
        announced = True
        deadline = None
        reason = None
        while True:
            if stop_requested:
                stop_requested = False
                request = b"K"
            elif select.select([control] if control is not None else [], [], [], 0.02)[
                0
            ]:
                request = os.read(control, 4096)
                if not request:
                    request = b"K"
                    reason = "owner_disconnected"
                    os.close(control)
                    control = None
            else:
                request = None
            if request:
                reason = reason or "stop_requested"
                force = b"K" in request
                os.killpg(child.pid, signal.SIGKILL if force else signal.SIGTERM)
                if force and deadline is None:
                    deadline = time.monotonic() + 2

            # Keep the leader unreaped until all descendants have drained. Its
            # zombie pins the process-group ID against reuse during signalling.
            root = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            children = (
                Path(f"/proc/self/task/{os.getpid()}/children").read_text().split()
            )
            remaining = False
            for value in children:
                pid = int(value)
                if pid != child.pid:
                    waited, _ = os.waitpid(pid, os.WNOHANG)
                    remaining |= waited == 0
            if root is not None and not remaining:
                # Orphans are adopted before their parent's exit is observable.
                # Re-snapshot after reaping parents: a grandchild may have just
                # become ours, and must not be mistaken for a completed group.
                children = (
                    Path(f"/proc/self/task/{os.getpid()}/children").read_text().split()
                )
                if children == [str(child.pid)]:
                    returncode = child.wait()
                    finished = True
                    record.update(
                        state="interrupted" if reason else "completed",
                        returncode=returncode,
                        reason=reason,
                        descendants_reaped=True,
                    )
                    save(path, record)
                    return
            if deadline is not None and time.monotonic() >= deadline:
                record.update(state="cleanup_unconfirmed", reason=reason)
                save(path, record)
                return
    except BaseException as error:
        record.update(
            state="cleanup_unconfirmed" if child else "not_started",
            error_type=type(error).__name__,
            errno=getattr(error, "errno", None),
        )
        save(path, record)
        raise
    finally:
        if child is not None and not finished:
            # The unreaped direct child still pins this group. Never signal a
            # group looked up later using an old journal PID.
            os.killpg(child.pid, signal.SIGKILL)
        if not announced:
            os.close(ready)
        if control is not None:
            os.close(control)


if __name__ == "__main__":
    supervise(int(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3]), sys.argv[4:])
