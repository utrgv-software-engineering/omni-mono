import json

import pytest

from omniagents.builtin.tools import task_list_tools as tlt


class _Ctx:
    def __init__(self, context=None):
        self.context = context or {}


@pytest.fixture(autouse=True)
def clean_state():
    tlt._TASKS_BY_SCOPE.clear()
    tlt._NEXT_ID_BY_SCOPE.clear()
    yield
    tlt._TASKS_BY_SCOPE.clear()
    tlt._NEXT_ID_BY_SCOPE.clear()


async def _call(tool, *args, **kwargs):
    return await tool._original_func(*args, **kwargs)


@pytest.mark.asyncio
async def test_create_list_update_roundtrip():
    ctx = _Ctx()
    out = await _call(tlt.task_create, ctx, subject="Do a thing", description="details")
    task = json.loads(str(out))["task"]
    assert task["id"] == "1"
    assert task["status"] == "pending"

    out = await _call(tlt.task_update, ctx, task_id="1", status="in_progress")
    assert json.loads(str(out))["task"]["status"] == "in_progress"

    out = await _call(tlt.task_list, ctx)
    payload = json.loads(str(out))
    assert payload["counts"]["in_progress"] == 1


@pytest.mark.asyncio
async def test_scopes_are_isolated():
    main_ctx = _Ctx({"task_scope": "main"})
    worker_ctx = _Ctx({"task_scope": "worker-1"})
    await _call(tlt.task_create, main_ctx, subject="parent task", description="d")
    out = await _call(tlt.task_list, worker_ctx)
    assert json.loads(str(out))["tasks"] == []


@pytest.mark.asyncio
async def test_dependency_edges_symmetric_and_delete_detaches():
    ctx = _Ctx()
    await _call(tlt.task_create, ctx, subject="a", description="d")
    await _call(tlt.task_create, ctx, subject="b", description="d")
    await _call(tlt.task_update, ctx, task_id="2", addBlockedBy=["1"])

    store = tlt._TASKS_BY_SCOPE["main"]
    assert store["1"]["blocks"] == ["2"]
    assert store["2"]["blockedBy"] == ["1"]

    await _call(tlt.task_update, ctx, task_id="1", status="deleted")
    assert "1" not in store
    assert store["2"]["blockedBy"] == []


@pytest.mark.asyncio
async def test_invalid_status_rejected():
    ctx = _Ctx()
    await _call(tlt.task_create, ctx, subject="a", description="d")
    out = await _call(tlt.task_update, ctx, task_id="1", status="bogus")
    assert "invalid status" in json.loads(str(out))["error"]
