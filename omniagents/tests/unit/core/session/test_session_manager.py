import asyncio

import pytest

from omniagents.core.session.manager import SessionManager


@pytest.mark.asyncio
async def test_cancel_cancels_task_and_clears_current_task():
    manager = SessionManager("project", "agent")
    session = manager.get_or_create()

    event = asyncio.Event()

    async def blocker() -> None:
        await event.wait()

    task = asyncio.create_task(blocker(), name="run-test")
    manager.bind_task(task, session)

    result = manager.cancel("run-test")
    await asyncio.sleep(0)

    assert result is True
    assert session.current_task is None

    with pytest.raises(asyncio.CancelledError):
        await task

    assert manager.cancel("run-test") is False
    assert manager.cancel("run-missing") is False
