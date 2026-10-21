from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Deque, Generic, Hashable, TypeVar

from .metrics import TransportMetrics, transport_metrics

ValueT = TypeVar("ValueT")


class QueuePolicy(str, Enum):
    CRITICAL = "critical"
    COALESCE_LATEST = "coalesce_latest"
    COALESCE_APPEND = "coalesce_append"
    DROP_OLDEST = "drop_oldest"


class TransportOverloadedError(Exception):
    def __init__(
        self,
        resource: str,
        *,
        unit: str,
        limit: int,
        retryable: bool = True,
        retry_after_ms: int | None = 500,
    ) -> None:
        super().__init__(f"{resource} queue exceeded {limit} {unit}")
        self.resource = resource
        self.unit = unit
        self.limit = limit
        self.retryable = retryable
        self.retry_after_ms = retry_after_ms if retryable else None


class QueueClosedError(Exception):
    pass


@dataclass
class _Entry(Generic[ValueT]):
    value: ValueT
    size: int
    policy: QueuePolicy
    key: Hashable | None
    event_class: str


class BoundedTransportQueue(Generic[ValueT]):
    def __init__(
        self,
        *,
        resource: str,
        max_messages: int,
        max_bytes: int,
        metrics: TransportMetrics = transport_metrics,
    ) -> None:
        if max_messages <= 0 or max_bytes <= 0:
            raise ValueError("queue limits must be positive")
        self.resource = resource
        self.max_messages = max_messages
        self.max_bytes = max_bytes
        self._metrics = metrics
        self._entries: Deque[_Entry[ValueT]] = deque()
        self._bytes = 0
        self._closed_error: BaseException | None = None
        self._condition = asyncio.Condition()
        self._update_gauges()

    @property
    def qsize(self) -> int:
        return len(self._entries)

    @property
    def byte_size(self) -> int:
        return self._bytes

    @property
    def closed(self) -> bool:
        return self._closed_error is not None

    async def put(
        self,
        value: ValueT,
        *,
        size: int,
        policy: QueuePolicy = QueuePolicy.CRITICAL,
        key: Hashable | None = None,
        event_class: str = "",
        merge: Callable[[ValueT, ValueT], ValueT] | None = None,
        merged_size: Callable[[ValueT], int] | None = None,
    ) -> None:
        if size < 0:
            raise ValueError("queue item size must be non-negative")
        async with self._condition:
            self._raise_if_closed()
            if size > self.max_bytes:
                self._record_overload(policy, event_class, "bytes")
                raise TransportOverloadedError(
                    self.resource,
                    unit="bytes",
                    limit=self.max_bytes,
                    retryable=False,
                    retry_after_ms=None,
                )
            if policy in {QueuePolicy.COALESCE_LATEST, QueuePolicy.COALESCE_APPEND}:
                index = self._coalescing_index(policy, key)
                if index is not None:
                    old = self._entries[index]
                    replacement = value
                    replacement_size = size
                    if policy is QueuePolicy.COALESCE_APPEND:
                        if merge is None or merged_size is None:
                            raise ValueError(
                                "append coalescing requires merge and merged_size"
                            )
                        replacement = merge(old.value, value)
                        replacement_size = merged_size(replacement)
                    if replacement_size <= self.max_bytes and (
                        self._bytes - old.size + replacement_size <= self.max_bytes
                    ):
                        self._entries[index] = _Entry(
                            replacement,
                            replacement_size,
                            policy,
                            key,
                            event_class,
                        )
                        self._bytes += replacement_size - old.size
                        self._metrics.increment(
                            "transport_coalesced_total",
                            resource=self.resource,
                            policy=policy.value,
                            event_class=event_class,
                        )
                        self._update_gauges()
                        self._condition.notify()
                        return
            if policy is QueuePolicy.DROP_OLDEST:
                self._drop_oldest_until_fits(size, event_class)
            self._raise_if_full(size, policy, event_class)
            self._entries.append(_Entry(value, size, policy, key, event_class))
            self._bytes += size
            self._metrics.increment(
                "transport_enqueued_total",
                resource=self.resource,
                policy=policy.value,
                event_class=event_class,
            )
            self._update_gauges()
            self._condition.notify()

    async def get(self) -> ValueT:
        async with self._condition:
            while not self._entries:
                self._raise_if_closed()
                await self._condition.wait()
            entry = self._entries.popleft()
            self._bytes -= entry.size
            self._update_gauges()
            return entry.value

    async def close(self, error: BaseException | None = None) -> None:
        async with self._condition:
            if self._closed_error is None:
                self._closed_error = error or QueueClosedError(
                    f"{self.resource} queue closed"
                )
                self._condition.notify_all()

    def _coalescing_index(
        self, policy: QueuePolicy, key: Hashable | None
    ) -> int | None:
        if key is None:
            return None
        if policy is QueuePolicy.COALESCE_APPEND:
            if not self._entries:
                return None
            trailing = self._entries[-1]
            if trailing.policy is policy and trailing.key == key:
                return len(self._entries) - 1
            return None
        for index in range(len(self._entries) - 1, -1, -1):
            if self._entries[index].key == key:
                return index
        return None

    def _drop_oldest_until_fits(self, size: int, event_class: str) -> None:
        while self._entries and (
            len(self._entries) >= self.max_messages
            or self._bytes + size > self.max_bytes
        ):
            drop_index = next(
                (
                    index
                    for index, entry in enumerate(self._entries)
                    if entry.policy is QueuePolicy.DROP_OLDEST
                ),
                None,
            )
            if drop_index is None:
                return
            dropped = self._entries[drop_index]
            del self._entries[drop_index]
            self._bytes -= dropped.size
            self._metrics.increment(
                "transport_dropped_total",
                resource=self.resource,
                policy=QueuePolicy.DROP_OLDEST.value,
                event_class=event_class or dropped.event_class,
            )

    def _raise_if_full(self, size: int, policy: QueuePolicy, event_class: str) -> None:
        if len(self._entries) >= self.max_messages:
            self._record_overload(policy, event_class, "messages")
            raise TransportOverloadedError(
                self.resource, unit="messages", limit=self.max_messages
            )
        if self._bytes + size > self.max_bytes:
            self._record_overload(policy, event_class, "bytes")
            raise TransportOverloadedError(
                self.resource, unit="bytes", limit=self.max_bytes
            )

    def _raise_if_closed(self) -> None:
        if self._closed_error is not None:
            raise self._closed_error

    def _record_overload(
        self, policy: QueuePolicy, event_class: str, outcome: str
    ) -> None:
        self._metrics.increment(
            "transport_overload_total",
            resource=self.resource,
            policy=policy.value,
            event_class=event_class,
            outcome=outcome,
        )

    def _update_gauges(self) -> None:
        self._metrics.set_gauge(
            "transport_queue_depth", len(self._entries), resource=self.resource
        )
        self._metrics.set_gauge(
            "transport_queue_bytes", self._bytes, resource=self.resource
        )
        self._metrics.observe_high_water(self._bytes, resource=self.resource)
