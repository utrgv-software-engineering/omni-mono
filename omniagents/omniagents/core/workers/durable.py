"""Worker recovery journal in the parent's transactional conversation inbox.

The same store transaction commits lifecycle changes and reporting obligations.
No backend-specific connection, serialized callback, or credentials are stored.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import time
import uuid
from typing import Any

LEASE_SECONDS = 30.0
TERMINAL = {"completed", "error", "cancelled"}
ACTIVE = {"accepted", "provisioning", "ready", "running", "resuming"}


class WorkerJournal:
    def __init__(self, store, parent_id: str, user_id: str | None):
        self.store, self.parent_id, self.user_id = store, parent_id, user_id

    def mutate(self, operation):
        thread = self.store.get_thread(self.parent_id)
        if thread is None or thread.user_id != self.user_id:
            raise PermissionError("Worker parent unavailable")

        def update(state, receipt):
            data = state.setdefault(
                "worker_recovery", {"version": 1, "workers": {}, "groups": {}}
            )
            if data["version"] != 1:
                raise ValueError("Unsupported worker journal version")
            return deepcopy(operation(data, state)), None

        return self.store.mutate_queue(self.parent_id, update)

    def rows(self):
        return self.mutate(lambda data, state: list(data["workers"].values()))

    def get(self, worker_id):
        return self.mutate(lambda data, state: data["workers"].get(worker_id))

    def admit(self, worker_id, operation_id, task, recipe, environment, owner):
        digest = hashlib.sha256(
            json.dumps([task, recipe, environment], sort_keys=True).encode()
        ).hexdigest()

        def update(data, state):
            for row in data["workers"].values():
                if row["operation_id"] == operation_id:
                    if row["digest"] != digest:
                        raise ValueError(
                            "Worker operation identity reused with different inputs"
                        )
                    return row
            if state.get("archived"):
                raise ValueError("Parent conversation is archived")
            if worker_id in data["workers"]:
                raise ValueError("Worker already exists")
            if len(data["workers"]) >= 512:
                raise ValueError("Worker history limit reached for this conversation")
            now = time.time()
            row = dict(
                worker_id=worker_id,
                operation_id=operation_id,
                digest=digest,
                session_id=uuid.uuid4().hex,
                parent_session_id=self.parent_id,
                user_id=self.user_id,
                task=task,
                recipe=recipe,
                environment=environment,
                status="accepted",
                generation=1,
                owner=owner,
                heartbeat=now,
                started_at=now,
                execution_after=now,
                updated_at=now,
                run_id="",
                result=None,
                error=None,
                report_status=None,
                cancel_requested=False,
            )
            data["workers"][worker_id] = row
            return row

        return self.mutate(update)

    def change(self, worker_id, generation, owner, **values):
        def update(data, state):
            row = data["workers"][worker_id]
            if row["generation"] != generation or row["owner"] != owner:
                raise RuntimeError("Worker ownership is no longer current")
            row.update(deepcopy(values), updated_at=time.time())
            return row

        return self.mutate(update)

    def heartbeat(self, worker_id, generation, owner):
        return self.change(worker_id, generation, owner, heartbeat=time.time())

    @staticmethod
    def _report(row, state):
        if row.get("report_status"):
            return
        report_id = f"worker:{row['worker_id']}:{row['generation']}"
        summary = (
            row.get("result") if row["status"] == "completed" else row.get("error")
        )
        state.setdefault("notifications", []).append(
            {
                "content": f"[worker] {row['worker_id']} status={row['status']} {summary or ''}\n\nDecide what to do next. Inspect recorded work before continuing interrupted actions.",
                "source": "worker.complete",
                "enqueued_at": time.time(),
                "worker_report": {
                    "worker_id": row["worker_id"],
                    "generation": row["generation"],
                    "id": report_id,
                },
            }
        )
        row["report_status"] = "queued"

    def finish(self, worker_id, generation, owner, status, result=None, error=None):
        if status not in TERMINAL:
            raise ValueError("Invalid terminal worker status")

        def update(data, state):
            row = data["workers"][worker_id]
            if row["generation"] != generation or row["owner"] != owner:
                raise RuntimeError("Worker ownership is no longer current")
            if row["status"] not in TERMINAL:
                row.update(
                    status=status,
                    result=result,
                    error=error,
                    finished_at=time.time(),
                    updated_at=time.time(),
                )
            self._report(row, state)
            return row

        return self.mutate(update)

    def interrupt(self, snapshot, *, status="interrupted", reason=None):
        def update(data, state):
            row = data["workers"][snapshot["worker_id"]]
            if (row["generation"], row["owner"], row["heartbeat"]) != (
                snapshot["generation"],
                snapshot["owner"],
                snapshot["heartbeat"],
            ):
                return row
            if (
                row["status"] in ACTIVE
                and time.time() - row["heartbeat"] > LEASE_SECONDS
            ):
                row.update(
                    owner=uuid.uuid4().hex,
                    status=status,
                    error=reason
                    or "Runtime interrupted; inspect recorded actions before resuming",
                    updated_at=time.time(),
                )
            return row

        return self.mutate(update)

    def resume(self, worker_id, operation_id, message, owner):
        def update(data, state):
            row = data["workers"][worker_id]
            if row.get("resume_operation") == operation_id:
                if row.get("resume_message") != message:
                    raise ValueError("Resume identity reused with different input")
                return row
            if row["status"] in ACTIVE:
                raise ValueError("Worker still has an active execution")
            if row.get("cleaned") or row["status"] == "blocked":
                raise ValueError(
                    row.get("error") or "Worker environment is unavailable"
                )
            row.update(
                generation=row["generation"] + 1,
                owner=owner,
                heartbeat=time.time(),
                status="resuming",
                execution_after=time.time(),
                resume_operation=operation_id,
                resume_message=message,
                run_id="",
                result=None,
                error=None,
                finished_at=None,
                report_status=None,
                cancel_requested=False,
                updated_at=time.time(),
            )
            return row

        return self.mutate(update)

    def cancel(self, worker_id):
        def update(data, state):
            row = data["workers"][worker_id]
            if row["status"] in TERMINAL:
                return row
            row["cancel_requested"] = True
            if row["status"] not in ACTIVE:
                row.update(
                    status="cancelled",
                    error="Cancelled by owner",
                    updated_at=time.time(),
                )
                self._report(row, state)
            return row

        return self.mutate(update)

    def groups(self):
        return self.mutate(lambda data, state: data["groups"])

    def save_group(self, key, value, revision=0):
        def update(data, state):
            old = data["groups"].get(key, {})
            if old.get("revision", 0) != revision:
                raise RuntimeError(
                    "Group changed in another runtime; reload before retrying"
                )
            row = {**deepcopy(value), "revision": revision + 1}
            data["groups"][key] = row
            return row["revision"]

        return self.mutate(update)


def for_session(service, session):
    return WorkerJournal(service._queue_store(session), session.id, session.user_id)
