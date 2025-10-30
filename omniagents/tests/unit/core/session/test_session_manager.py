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

    result = await manager.cancel("run-test")
    await asyncio.sleep(0)

    assert result is True
    assert session.current_task is None

    with pytest.raises(asyncio.CancelledError):
        await task

    assert await manager.cancel("run-test") is False
    assert await manager.cancel("run-missing") is False


@pytest.mark.asyncio
async def test_cancel_uses_stream_handle_before_task_cancel():
    manager = SessionManager("project", "agent")
    session = manager.get_or_create()
    release = asyncio.Event()

    class FakeStream:
        def __init__(self):
            self.modes = []

        async def cancel(self, mode: str):
            self.modes.append(mode)
            release.set()

    stream = FakeStream()

    async def runner() -> None:
        session.current_stream_result = stream
        try:
            await release.wait()
        finally:
            session.current_stream_result = None

    task = asyncio.create_task(runner(), name="run-stream")
    manager.bind_task(task, session)
    session.current_stream_result = stream

    result = await manager.cancel("run-stream")

    assert result is True
    assert stream.modes == ["immediate"]
    assert task.done() is True
    assert task.cancelled() is False
    assert session.current_task is None
    assert session.current_stream_result is None
    assert await manager.cancel("run-stream") is False
