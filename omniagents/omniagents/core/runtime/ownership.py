"""Exclusive durable run reservations, not time-based leases.

Only the holder can release a reservation after its worker stops. A crash leaves
it reserved: elapsed time, a missing local task, or a terminal UI projection are
not evidence that a remote process can no longer execute. No takeover API exists.
"""

from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import dataclass
import uuid

current_ownership = ContextVar("runtime_ownership", default=None)


@dataclass
class RunOwnership:
    store: object
    thread_id: str
    run_id: str
    owner: str
    generation: int
    transferred: bool = False
    entered: bool = False
    released: bool = False
    closing: bool = False
    active_operations: int = 0
    execution_uncertain: bool = False

    def _matches(self, record):
        return record == {
            "run_id": self.run_id,
            "owner": self.owner,
            "generation": self.generation,
        }

    def assert_current(self):
        def check(state, receipt):
            if (
                self.released
                or self.closing
                or not self._matches(state.get("runtime_owner"))
            ):
                raise RuntimeError(
                    "Run ownership is no longer current; execution refused"
                )
            return None, None

        self.store.mutate_queue(self.thread_id, check)

    @contextmanager
    def operation(self):
        """Track admitted local I/O until it settles, even during cancellation."""
        self.assert_current()
        self.active_operations += 1
        try:
            yield
        finally:
            self.active_operations -= 1
            if self.closing and self.active_operations == 0:
                self.release()

    def release(self):
        if self.released:
            return
        self.closing = True
        if self.active_operations or self.execution_uncertain:
            # Closing rejects new work immediately. The last admitted operation
            # releases only if cleanup is confirmed; a coroutine ending is not
            # an I/O fence. Unknown execution must survive local release retries.
            return

        def finish(state, receipt):
            if state.get("runtime_owner") is None and self._matches(
                state.get("last_runtime_owner")
            ):
                return None, None  # Commit succeeded but its acknowledgment was lost.
            if not self._matches(state.get("runtime_owner")):
                raise RuntimeError("Cannot release another runtime's run reservation")
            state["last_runtime_owner"] = state["runtime_owner"]
            state["runtime_owner"] = None
            return None, None

        self.store.mutate_queue(self.thread_id, finish)
        self.released = True


def reserve_run(store, thread_id):
    from omniagents.core.session.queue_inbox import current_dispatch

    owner = uuid.uuid4().hex
    run_id = f"run_{uuid.uuid4().hex}"

    def claim(state, receipt):
        if state.get("archived"):
            raise RuntimeError("Session is archived")
        if state.get("runtime_owner") is not None:
            raise RuntimeError(
                "Session has an active or unresolved runtime owner; "
                "starting another run could duplicate actions"
            )
        for entry in state["items"]:
            if entry["owner"] is not None and current_dispatch.get() != (
                thread_id,
                entry["item"]["id"],
                entry["owner"],
            ):
                raise RuntimeError(
                    "Session has an active or unresolved queue dispatcher; "
                    "starting another run could duplicate actions"
                )
        generation = state.get("runtime_generation", 0) + 1
        state["runtime_generation"] = generation
        state["runtime_owner"] = {
            "run_id": run_id,
            "owner": owner,
            "generation": generation,
        }
        return generation, None

    generation = store.mutate_queue(thread_id, claim)
    return RunOwnership(store, thread_id, run_id, owner, generation)
