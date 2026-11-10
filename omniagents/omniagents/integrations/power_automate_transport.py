from __future__ import annotations

import json
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


class InvalidDelivery(ValueError):
    pass


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidDelivery("invalid_json")
        result[key] = value
    return result


def _constant(value: str) -> Any:
    raise InvalidDelivery("invalid_json")


def _float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise InvalidDelivery("invalid_json")
    return number


def _json(text: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_object,
            parse_constant=_constant,
            parse_float=_float,
        )
    except (ValueError, RecursionError):
        raise InvalidDelivery("invalid_json") from None


@dataclass(frozen=True)
class Delivery:
    id: str
    source: str
    payload: dict


def load_delivery(event_id: str, source: str, raw_payload: str) -> Delivery:
    if not isinstance(raw_payload, str):
        raise InvalidDelivery("invalid_payload")
    payload = _json(raw_payload)
    if not isinstance(payload, dict):
        raise InvalidDelivery("invalid_payload")
    return Delivery(event_id, source, payload)


async def poll_deliveries(
    connection,
    sources: set[str],
    accept: Callable[[Delivery], Awaitable[Any]],
    limit: int = 100,
    not_before: str | None = None,
) -> None:
    if type(limit) is not int or limit < 0:
        raise ValueError("invalid_poll_limit")
    if not sources:
        return
    authorized_sources = sorted(sources)
    cutoff_clause = ""
    parameters = (authorized_sources,)
    if not_before is not None:
        from .power_automate_policy import parse_timestamp

        cutoff_clause = "AND e.created_at >= %s "
        parameters = (authorized_sources, parse_timestamp(not_before))
    for _ in range(limit):
        async with connection.transaction():
            cursor = await connection.execute(
                "SELECT e.id, e.source, e.payload FROM omni_bridge.inbound_events e "
                "WHERE e.accepted_at IS NULL AND e.rejected_at IS NULL "
                "AND e.available_at <= now() AND e.source=ANY(%s) "
                + cutoff_clause
                + "ORDER BY e.available_at, e.created_at, e.id "
                "LIMIT 1 FOR UPDATE OF e SKIP LOCKED",
                parameters,
            )
            row = await cursor.fetchone()
            if row is None:
                return
            event_id, source, raw_payload = row
            error_code = None
            try:
                delivery = load_delivery(event_id, source, raw_payload)
            except InvalidDelivery:
                error_code = "invalid_delivery"
            else:
                try:
                    async with connection.transaction():
                        await accept(delivery)
                except ValueError:
                    error_code = "accept_rejected"
            if error_code is not None:
                await connection.execute(
                    "UPDATE omni_bridge.inbound_events "
                    "SET rejected_at=now(), error_code=%s WHERE id=%s AND source=%s",
                    (error_code, event_id, source),
                )
            else:
                await connection.execute(
                    "UPDATE omni_bridge.inbound_events "
                    "SET accepted_at=now(), error_code=NULL WHERE id=%s AND source=%s",
                    (event_id, source),
                )
