"""Automation turns on the same service and durable queue as Personal chat."""

import asyncio
import logging
import uuid
import time

from omniagents.rpc.base import _current_channel
from omniagents.rpc.server import JsonRpcChannel
from .intake import RunResult, RunStep

logger = logging.getLogger(__name__)


class _Sink:
    """An in-process connection: nothing is received over it."""

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def send_json(self, data):
        pass  # Canonical snapshots and the run-end listener retain the result.

    async def receive_json(self):
        raise RuntimeError("an in-process connection receives nothing")

    def is_connected(self) -> bool:
        return True


class _Steps(_Sink):
    """The conversation's live events, told to ``progress`` as this run's steps.

    The conversation may run another turn first (a chat queued ahead), so
    steps are held until the run's id is known and only its own are told.
    They are told in order from a task of their own: a slow report (a Linear
    session) never holds up the run, and a failing one never detaches us.
    """

    def __init__(self, progress):
        self.progress = progress
        self.run_id = None
        self.held = []
        self.steps: asyncio.Queue[RunStep | None] = asyncio.Queue()
        self.reporter = asyncio.ensure_future(self._report())

    async def send_json(self, data):
        params = data.get("params") or {}
        method = data.get("method")
        if method == "tool_called":
            step = RunStep("tool", name=params.get("tool") or "a tool")
        elif method == "message_output" and str(params.get("content") or "").strip():
            step = RunStep("message", text=params["content"])
        else:
            return
        if self.run_id is None:
            self.held.append((params.get("run_id"), step))
        elif params.get("run_id") == self.run_id:
            self.steps.put_nowait(step)

    def mine(self, run_id):
        if self.run_id is not None:
            return
        self.run_id = run_id
        for held_run, step in self.held:
            if held_run == run_id:
                self.steps.put_nowait(step)
        self.held = []

    async def _report(self):
        while (step := await self.steps.get()) is not None:
            try:
                await self.progress(step)
            except Exception:
                logger.exception("integrations: reporting a step failed")

    async def drain(self):
        self.steps.put_nowait(None)
        await self.reporter

    def cancel(self):
        self.reporter.cancel()


class ConversationRunner:
    def __init__(self, service, principal, agent):
        self.agent = agent
        self.service = service
        self.principal = principal

    async def resume(self, session_id):
        channel = JsonRpcChannel(_Sink())
        channel.principal = await self.principal()
        token = _current_channel.set(channel)
        try:
            await self.service.server_call(
                "session.ensure", {"session_id": session_id}, session_id
            )
            await self.service.queue_status(session_id)
        finally:
            _current_channel.reset(token)

    async def __call__(self, agent, session_id, prompt, progress=None):
        from .server import run_failure

        if agent != self.agent:
            raise ValueError("Conversation agent does not match its runtime")
        service = self.service
        steps = _Steps(progress) if progress is not None else None
        channel = JsonRpcChannel(steps or _Sink())
        channel.principal = await self.principal()
        token = _current_channel.set(channel)
        ends = {}
        queued = None
        run_id = None

        async def ended(end):
            # Capture the answer before the queue starts a later user's turn.
            from .grading import thread_history

            store = service._conversation_service().store
            turn = store.get_turn(session_id, end["run_id"])
            history = thread_history(
                store.list_items(session_id, turn_id=end["run_id"], limit=100000)
            )
            output = next(
                (
                    item["content"]
                    for item in reversed(history)
                    if item.get("role") == "assistant" and item.get("content")
                ),
                None,
            )
            ends[end["run_id"]] = RunResult(
                history=history,
                started_at=turn.created_at if turn else time.time(),
                finished_at=(
                    turn.completed_at if turn and turn.completed_at else time.time()
                ),
                error=run_failure(end),
                output=output,
                stopped=end.get("end_reason") in {"cancelled", "stopped"},
            )

        service.add_run_end_listener(session_id, ended)
        try:
            await service.server_call(
                "session.ensure", {"session_id": session_id}, session_id
            )
            queued = await service.enqueue_message(
                session_id,
                prompt,
                source="automation",
                submission_id=str(uuid.uuid4()),
            )
            if not queued.get("ok"):
                raise RuntimeError(
                    f"Personal queue rejected event: {queued.get('reason')}"
                )
            while True:
                state = await service.queue_status(
                    session_id,
                    submission_id=f"queue-dispatch:{queued['id']}",
                )
                receipt = state.get("submission") or {}
                if receipt.get("status") == "completed":
                    run_id = receipt["result"]["run_id"]
                    if steps:
                        steps.mine(run_id)
                    if run_id in ends:
                        if steps:
                            await steps.drain()
                        return ends[run_id]
                elif receipt.get("status") in {"failed", "unknown"}:
                    raise RuntimeError(
                        receipt.get("error", "Automation outcome unresolved")
                    )
                queue = await service.list_queue(session_id)
                for item in queue.get("items", []):
                    if item.get("id") == queued["id"] and item.get("state") == "failed":
                        raise RuntimeError(
                            item.get("error", "Automation could not start")
                        )
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            # Cancel this event only, never whichever other user's turn is active.
            if queued:
                state = await service.queue_status(
                    session_id, submission_id=f"queue-dispatch:{queued['id']}"
                )
                receipt = state.get("submission") or {}
                run_id = (receipt.get("result") or {}).get("run_id")
                if run_id and state.get("active_run_id") == run_id:
                    await service.stop_run(run_id)
                else:
                    await service.cancel_queued_message(session_id, queued["id"])
            raise
        finally:
            if steps:
                steps.cancel()
            service.remove_run_end_listener(session_id, ended)
            session = service._session_manager.get(session_id)
            if session and channel in session.channels:
                session.channels.remove(channel)
            _current_channel.reset(token)


# Compatibility for existing imports; routing has no role-specific behavior.
PersonalRunner = ConversationRunner
