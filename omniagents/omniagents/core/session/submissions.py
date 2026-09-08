"""Durable at-most-once RPC submission receipts, scoped to a conversation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid


async def submit_once(session, store, submission_id, payload, invoke):
    if not isinstance(submission_id, str) or not 1 <= len(submission_id) <= 128:
        raise ValueError(
            "submission_id must be a nonempty string of at most 128 characters"
        )
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    tasks = getattr(session, "submission_tasks", None)
    if tasks is None:
        tasks = session.submission_tasks = {}
    owner = uuid.uuid4().hex
    claim = {"digest": digest, "status": "pending", "owner": owner}
    receipt = store.submission_receipt(session.id, submission_id, claim=claim)
    if receipt["digest"] != digest:
        raise ValueError("submission_id was already used for different content")
    if receipt["status"] == "completed":
        return receipt["result"]
    if receipt["status"] == "failed":
        raise RuntimeError(receipt["error"])
    if submission_id in tasks:
        return await asyncio.shield(tasks[submission_id])
    if receipt["owner"] != owner:
        # A different process reserved this operation. Reissuing it could
        # duplicate a run after a crash between acceptance and receipt commit.
        raise RuntimeError(
            "Submission outcome is unresolved; it will not be sent twice"
        )

    async def run():
        def execution_state():
            return (
                getattr(session, "current_task", None),
                getattr(getattr(session, "event_log", None), "last_seq", None),
                tuple(item.id for item in getattr(session, "message_queue", [])),
            )

        before = execution_state()
        try:
            result = await invoke()
        except Exception as error:
            store.submission_receipt(
                session.id,
                submission_id,
                # An exception after observable execution began is not proof
                # of rejection. Never authorize a fresh key in that case.
                result={
                    **claim,
                    "status": "failed" if execution_state() == before else "unknown",
                    "error": str(error),
                },
            )
            raise
        store.submission_receipt(
            session.id,
            submission_id,
            result={**claim, "status": "completed", "result": result},
        )
        return result

    task = asyncio.create_task(run())
    tasks[submission_id] = task

    # The RPC caller can disappear while the accepted work must finish.
    def finished(done):
        tasks.pop(submission_id, None)
        if not done.cancelled():
            done.exception()

    task.add_done_callback(finished)
    return await asyncio.shield(task)
