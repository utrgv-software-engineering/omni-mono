import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from omniagents.core.conversation.store import SqliteConversationStore
from omniagents.core.session.submissions import submit_once
from omniagents.core.tools.client_function import human_reply_outputs


@pytest.mark.asyncio
async def test_lost_caller_and_repeated_requests_execute_once(tmp_path):
    store = SqliteConversationStore(path=str(tmp_path / "receipts.db"))
    session = SimpleNamespace(id="A")
    gate = asyncio.Event()
    entered = asyncio.Event()
    calls = []

    async def invoke():
        calls.append(1)
        entered.set()
        await gate.wait()
        return {"run_id": "one"}

    caller = asyncio.create_task(
        submit_once(session, store, "key", {"prompt": "hello"}, invoke)
    )
    await entered.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    retry = asyncio.create_task(
        submit_once(session, store, "key", {"prompt": "hello"}, invoke)
    )
    gate.set()
    assert await retry == {"run_id": "one"}
    assert calls == [1]
    store.close()
    # Accepted receipts survive both service and store reconstruction.
    reopened = SqliteConversationStore(path=str(tmp_path / "receipts.db"))
    assert await submit_once(
        SimpleNamespace(id="A"), reopened, "key", {"prompt": "hello"}, invoke
    ) == {"run_id": "one"}
    with pytest.raises(ValueError, match="different content"):
        await submit_once(session, reopened, "key", {"prompt": "different"}, invoke)
    reopened.close()


@pytest.mark.asyncio
async def test_foreign_pending_receipt_never_reexecutes(tmp_path):
    store = SqliteConversationStore(path=str(tmp_path / "receipts.db"))
    session = SimpleNamespace(id="A")
    invoke = AsyncMock(return_value={"ok": True})
    # Reserve and simulate interruption before the operation starts.
    import hashlib

    store.submission_receipt(
        "A",
        "key",
        claim={
            "owner": "old-process",
            "status": "pending",
            "digest": hashlib.sha256(b"{}").hexdigest(),
        },
    )
    with pytest.raises(RuntimeError, match="unresolved"):
        await submit_once(session, store, "key", {}, invoke)
    invoke.assert_not_called()
    store.close()


def test_human_reply_returns_real_multimodal_tool_outputs():
    outputs = human_reply_outputs(
        {
            "input_content": [
                {"type": "input_text", "text": "see these"},
                {"type": "input_image", "image_url": "data:image/png;base64,eA=="},
                {"type": "input_file", "filename": "note.txt", "file_data": "eA=="},
            ]
        }
    )
    assert [part.type for part in outputs] == ["text", "image", "file"]
    assert outputs[1].image_url.endswith("eA==")
    assert outputs[2].filename == "note.txt"


@pytest.mark.asyncio
async def test_failure_after_execution_is_not_reported_as_rejection(tmp_path):
    store = SqliteConversationStore(path=str(tmp_path / "receipts.db"))
    session = SimpleNamespace(id="A", event_log=SimpleNamespace(last_seq=0))

    async def invoke():
        session.event_log.last_seq += 1
        raise RuntimeError("lost confirmation")

    with pytest.raises(RuntimeError, match="lost confirmation"):
        await submit_once(session, store, "key", {}, invoke)
    assert store.submission_receipt("A", "key")["status"] == "unknown"
    store.delete_thread("A")
    assert store.submission_receipt("A", "key") is None
    store.close()
