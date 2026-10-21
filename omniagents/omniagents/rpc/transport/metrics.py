from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from threading import Lock
from typing import Mapping


@dataclass(frozen=True)
class MetricKey:
    name: str
    resource: str
    policy: str = ""
    event_class: str = ""
    outcome: str = ""


class TransportMetrics:
    def __init__(self) -> None:
        self._values: dict[MetricKey, int] = defaultdict(int)
        self._lock = Lock()

    def set_gauge(self, name: str, value: int, *, resource: str) -> None:
        key = MetricKey(name=name, resource=resource)
        with self._lock:
            self._values[key] = value

    def increment(
        self,
        name: str,
        *,
        resource: str,
        value: int = 1,
        policy: str = "",
        event_class: str = "",
        outcome: str = "",
    ) -> None:
        key = MetricKey(
            name=name,
            resource=resource,
            policy=policy,
            event_class=event_class,
            outcome=outcome,
        )
        with self._lock:
            self._values[key] += value

    def observe_high_water(self, value: int, *, resource: str) -> None:
        key = MetricKey(name="transport_queue_high_water", resource=resource)
        with self._lock:
            self._values[key] = max(self._values[key], value)

    def snapshot(self) -> Mapping[MetricKey, int]:
        with self._lock:
            return dict(self._values)

    def reset(self) -> None:
        with self._lock:
            self._values.clear()


transport_metrics = TransportMetrics()
