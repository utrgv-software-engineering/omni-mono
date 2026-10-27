"""Automation turns on the same service and durable queue as Personal chat."""

import asyncio
import uuid
import time

from omniagents.rpc.base import _current_channel
from omniagents.rpc.server import JsonRpcChannel
from .architecture import PERSONAL_SESSION
from .intake import RunResult


class _Sink:
    async def send_json(self, message):
        pass  # Canonical snapshots and the run-end listener retain the result.


class PersonalRunner:
    def __init__(self, service, principal):
        self.service = service
        self.principal = principal

    async def resume(self):
        channel = JsonRpcChannel(_Sink())
        channel.principal = await self.principal()
        token = _current_channel.set(channel)
        try:
            await self.service.server_call(
                "session.ensure", {"session_id": PERSONAL_SESSION}, PERSONAL_SESSION
            )
            await self.service.queue_status(PERSONAL_SESSION)
        finally:
            _current_channel.reset(token)

    async def __call__(self, agent, session_id, prompt, progress=None):
        from .server import run_failure

        if agent != "generalist" or session_id != PERSONAL_SESSION:
            raise ValueError("Personal automation must use the shared generalist")
        service = self.service
        channel = JsonRpcChannel(_Sink())
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
                source="automation:personal",
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
                    if run_id in ends:
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
            service.remove_run_end_listener(session_id, ended)
            session = service._session_manager.get(session_id)
            if session and channel in session.channels:
                session.channels.remove(channel)
            _current_channel.reset(token)
