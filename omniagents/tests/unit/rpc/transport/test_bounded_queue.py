import asyncio

import pytest

from omniagents.rpc.transport.metrics import TransportMetrics
from omniagents.rpc.transport.queue import (
    BoundedTransportQueue,
    QueueClosedError,
    QueuePolicy,
    TransportOverloadedError,
)


@pytest.mark.asyncio
async def test_queue_enforces_message_and_byte_limits():
    queue = BoundedTransportQueue[str](resource="test", max_messages=2, max_bytes=5)

    await queue.put("ab", size=2)
    await queue.put("cd", size=2)

    with pytest.raises(TransportOverloadedError) as exc_info:
        await queue.put("e", size=1)

    assert exc_info.value.unit == "messages"
    assert queue.qsize == 2
    assert queue.byte_size == 4


@pytest.mark.asyncio
async def test_latest_replacement_preserves_position_and_accounting():
    queue = BoundedTransportQueue[str](resource="test", max_messages=3, max_bytes=10)

    await queue.put("old", size=3, policy=QueuePolicy.COALESCE_LATEST, key="status")
    await queue.put("critical", size=4)
    await queue.put("new", size=3, policy=QueuePolicy.COALESCE_LATEST, key="status")

    assert queue.qsize == 2
    assert queue.byte_size == 7
    assert await queue.get() == "new"
    assert await queue.get() == "critical"


@pytest.mark.asyncio
async def test_append_coalescing_is_lossless():
    queue = BoundedTransportQueue[str](resource="test", max_messages=2, max_bytes=10)

    await queue.put(
        "ab",
        size=2,
        policy=QueuePolicy.COALESCE_APPEND,
        key="token",
        merge=lambda current, incoming: current + incoming,
        merged_size=len,
    )
    await queue.put(
        "cd",
        size=2,
        policy=QueuePolicy.COALESCE_APPEND,
        key="token",
        merge=lambda current, incoming: current + incoming,
        merged_size=len,
    )

    assert queue.qsize == 1
    assert queue.byte_size == 4
    assert await queue.get() == "abcd"


@pytest.mark.asyncio
async def test_append_coalescing_does_not_cross_critical_event():
    queue = BoundedTransportQueue[str](resource="test", max_messages=3, max_bytes=10)
    append_options = {
        "policy": QueuePolicy.COALESCE_APPEND,
        "key": "token",
        "merge": lambda current, incoming: current + incoming,
        "merged_size": len,
    }

    await queue.put("a", size=1, **append_options)
    await queue.put("run_end", size=3)
    await queue.put("c", size=1, **append_options)

    assert queue.qsize == 3
    assert await queue.get() == "a"
    assert await queue.get() == "run_end"
    assert await queue.get() == "c"


@pytest.mark.asyncio
async def test_append_coalescing_does_not_cross_different_key():
    queue = BoundedTransportQueue[str](resource="test", max_messages=3, max_bytes=10)

    for value, key in (("a", "first"), ("b", "second"), ("c", "first")):
        await queue.put(
            value,
            size=1,
            policy=QueuePolicy.COALESCE_APPEND,
            key=key,
            merge=lambda current, incoming: current + incoming,
            merged_size=len,
        )

    assert queue.qsize == 3
    assert [await queue.get(), await queue.get(), await queue.get()] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_drop_oldest_never_evicts_critical_entries():
    queue = BoundedTransportQueue[str](resource="test", max_messages=2, max_bytes=10)

    await queue.put("critical", size=4)
    await queue.put("audio-1", size=3, policy=QueuePolicy.DROP_OLDEST)
    await queue.put("audio-2", size=3, policy=QueuePolicy.DROP_OLDEST)

    assert await queue.get() == "critical"
    assert await queue.get() == "audio-2"


@pytest.mark.asyncio
async def test_close_wakes_waiters_without_queue_capacity():
    queue = BoundedTransportQueue[str](resource="test", max_messages=1, max_bytes=1)
    waiter = asyncio.create_task(queue.get())
    await asyncio.sleep(0)

    await queue.close()

    with pytest.raises(QueueClosedError):
        await waiter


@pytest.mark.asyncio
async def test_metrics_are_low_cardinality_and_atomic_with_mutations():
    metrics = TransportMetrics()
    queue = BoundedTransportQueue[str](
        resource="test", max_messages=1, max_bytes=4, metrics=metrics
    )

    await queue.put("a", size=1, event_class="critical")
    with pytest.raises(TransportOverloadedError):
        await queue.put("b", size=1, event_class="critical")
    await queue.get()

    snapshot = metrics.snapshot()
    values = {
        (key.name, key.resource, key.outcome): value for key, value in snapshot.items()
    }
    assert values[("transport_queue_depth", "test", "")] == 0
    assert values[("transport_queue_high_water", "test", "")] == 1
    assert values[("transport_overload_total", "test", "messages")] == 1


def test_queue_rejects_invalid_limits():
    with pytest.raises(ValueError):
        BoundedTransportQueue[str](resource="test", max_messages=0, max_bytes=1)
