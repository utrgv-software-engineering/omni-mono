"""Cooperative run admission at the concrete Workspace I/O boundary.

Cached Workspace objects and subclass overrides share this guard. It cannot
intercept arbitrary Python/remote side effects outside the Workspace contract.
"""

import asyncio
from functools import wraps

from ._process import await_cleanup


def guard_operation(method):
    @wraps(method)
    async def guarded(*args, **kwargs):
        from omniagents.core.runtime.ownership import current_ownership

        owner = current_ownership.get()
        if owner is None:
            return await method(*args, **kwargs)
        with owner.operation():
            if method.__name__ in ("write_text", "write_bytes", "mkdir", "remove"):
                # Host writes use to_thread: cancelling their await does not
                # stop the write. Hold admission until the actual write settles.
                return await await_cleanup(asyncio.create_task(method(*args, **kwargs)))
            return await method(*args, **kwargs)

    return guarded
