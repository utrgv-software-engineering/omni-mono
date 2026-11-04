"""Short, administrator-owned idle leases for replacing a factory runtime."""

import asyncio
import functools
import secrets
import time


class ReleaseGate:
    def __init__(self, busy, clock=time.monotonic):
        self.busy = busy
        self.clock = clock
        self.admissions = 0
        self.owner = None
        self.until = 0

    @property
    def paused(self):
        return bool(self.owner and self.clock() < self.until)

    def acquire(self):
        # No await between the idle check and admission pause: all admissions
        # and this operation execute on the runtime's event loop.
        if self.paused or self.admissions or self.busy():
            raise RuntimeError("Factory has active or queued work")
        self.owner = secrets.token_urlsafe(32)
        self.until = self.clock() + 120
        return self.owner

    def release(self, owner):
        if self.owner != owner:
            raise ValueError("Unknown release lease")
        self.owner = None
        self.until = 0

    async def wait(self):
        while self.paused:
            await asyncio.sleep(0.1)


def admission(method):
    @functools.wraps(method)
    async def guarded(self, *args, **kwargs):
        from omniagents.core.runtime.factory_stop import raise_if_stopped

        raise_if_stopped()
        gate = getattr(self, "_release_gate", None)
        if gate is None:
            return await method(self, *args, **kwargs)
        if gate.paused:
            raise RuntimeError("Factory is activating a release; retry shortly")
        gate.admissions += 1
        try:
            return await method(self, *args, **kwargs)
        finally:
            gate.admissions -= 1

    return guarded
