"""Run-owned live cells with durable outcomes. JavaScript stacks are never replayed."""

from __future__ import annotations
import asyncio
import json
import uuid
from typing import Any
from .runtime import execute, TRIPWIRES
from omniagents.core.tools.result_store import _connect

_live: dict[tuple, asyncio.Task] = {}
_fatal: dict[tuple, Exception] = {}


def raise_fatal(session_id, run_id):
    for key, error in _fatal.items():
        if key[-2:] == (session_id, run_id):
            raise error


def _db(scope, session_id):
    db = _connect(scope, session_id)
    db.execute(
        "CREATE TABLE IF NOT EXISTS javascript_cells (id TEXT PRIMARY KEY, run_id TEXT, status TEXT, result TEXT)"
    )
    return db


def _save(scope, session_id, cell_id, run_id, status, result):
    db = _db(scope, session_id)
    try:
        with db:
            db.execute(
                "INSERT INTO javascript_cells VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,result=excluded.result",
                (cell_id, run_id, status, json.dumps(result)),
            )
    finally:
        db.close()


def _read(scope, session_id, cell_id):
    db = _db(scope, session_id)
    try:
        row = db.execute(
            "SELECT * FROM javascript_cells WHERE id=?", (cell_id,)
        ).fetchone()
        if row is None:
            raise ValueError("JavaScript cell is unavailable in this session")
        return dict(row)
    finally:
        db.close()


def _timeout(value):
    if type(value) is not int or not 0 <= value <= 30_000:
        raise ValueError("wait time must be between 0 and 30000 milliseconds")
    return value / 1000


async def start(dispatcher, code, yield_time_ms=1000):
    _timeout(yield_time_ms)
    dispatcher.hooks.assert_owner()
    scope, session_id, run_id = (
        dispatcher.scope,
        dispatcher.session.id,
        dispatcher.hooks.run_id,
    )
    prefix = (*scope, session_id)
    if sum(key[:3] == prefix and not task.done() for key, task in _live.items()) >= 4:
        raise RuntimeError("At most four JavaScript cells may run in one session")
    cell_id = uuid.uuid4().hex
    key = (*prefix, cell_id)
    state: dict[str, Any] = {"output": "", "errors": []}
    _save(scope, session_id, cell_id, run_id, "running", state)

    async def output(value):
        state.update(value)
        _save(scope, session_id, cell_id, run_id, "running", state)

    async def run():
        status = "completed"
        try:
            state.update(await execute(code, dispatcher, on_output=output))
            if state["errors"]:
                status = "failed"
        except TRIPWIRES as exc:
            status = "failed"
            state["errors"].append(str(exc))
            _fatal[(*scope, session_id, run_id)] = exc
        except asyncio.CancelledError:
            status = "cancelled"
            state["errors"].append(
                "Cell cancelled; dispatched tools must not be replayed"
            )
        except Exception as exc:
            status = "failed"
            state["errors"].append(str(exc)[:1000])
        finally:
            try:
                _save(scope, session_id, cell_id, run_id, status, state)
            finally:
                _live.pop(key, None)

    _live[key] = asyncio.create_task(run(), name=f"javascript:{session_id}:{cell_id}")
    setattr(_live[key], "_cell_run_id", run_id)
    return await wait(dispatcher, cell_id, yield_time_ms)


async def wait(dispatcher, cell_id, timeout_ms=1000, offset=0):
    seconds = _timeout(timeout_ms)
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative character offset")
    dispatcher.hooks.assert_owner()
    scope, session_id = dispatcher.scope, dispatcher.session.id
    row = _read(scope, session_id, cell_id)
    task = _live.get((*scope, session_id, cell_id))
    if task is not None:
        # A later run cannot adopt a still-live cell from an earlier owner.
        if row["run_id"] != dispatcher.hooks.run_id:
            raise ValueError("Cell belongs to a different active run")
        await asyncio.wait({task}, timeout=seconds)
        row = _read(scope, session_id, cell_id)
    elif row["status"] == "running":
        state = json.loads(row["result"])
        state["errors"].append("Runtime interrupted; JavaScript was not replayed")
        _save(scope, session_id, cell_id, row["run_id"], "interrupted", state)
        row = _read(scope, session_id, cell_id)
    raise_fatal(session_id, dispatcher.hooks.run_id)
    result = json.loads(row["result"])
    value = result.get("output", "")
    return {
        **result,
        "cell_id": cell_id,
        "status": row["status"],
        "output": value[offset:],
        "next_offset": len(value),
    }


async def cancel(dispatcher, cell_id):
    await wait(dispatcher, cell_id, 0)
    task = _live.get((*dispatcher.scope, dispatcher.session.id, cell_id))
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return await wait(dispatcher, cell_id, 0)


async def close_run(session_id, run_id):
    tasks = []
    for key, task in list(_live.items()):
        if key[2] != session_id:
            continue
        if getattr(task, "_cell_run_id", None) == run_id:
            task.cancel()
            tasks.append(task)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    for key in list(_fatal):
        if key[-2:] == (session_id, run_id):
            _fatal.pop(key, None)


def list_cells(scope, session_id, after=0, limit=50):
    if (
        type(after) is not int
        or after < 0
        or type(limit) is not int
        or not 1 <= limit <= 100
    ):
        raise ValueError("after must be nonnegative and limit must be 1..100")
    db = _db(scope, session_id)
    try:
        rows = db.execute(
            "SELECT rowid AS seq,id,run_id,status FROM javascript_cells WHERE rowid>? ORDER BY rowid LIMIT ?",
            (after, limit + 1),
        ).fetchall()
        output = [dict(row) for row in rows[:limit]]
        for row in output:
            if (
                row["status"] == "running"
                and (*scope, session_id, row["id"]) not in _live
            ):
                row["status"] = "interrupted"
        return {
            "cells": output,
            "next_after": output[-1]["seq"] if len(rows) > limit else None,
        }
    finally:
        db.close()
