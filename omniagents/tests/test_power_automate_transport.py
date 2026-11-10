import copy
from contextlib import asynccontextmanager

import pytest

from omniagents.integrations.power_automate_transport import (
    InvalidDelivery,
    load_delivery,
    poll_deliveries,
)


@pytest.mark.parametrize(
    "payload",
    ["{}", '{"arbitrary":[null,true,2]}', '{"thread_history":{"anything":1}}'],
)
def test_plain_json_object(payload):
    delivery = load_delivery("id", "source", payload)
    assert delivery.id == "id"
    assert delivery.source == "source"
    assert isinstance(delivery.payload, dict)


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        "null",
        "1",
        '"text"',
        "{",
        '{"x":NaN}',
        '{"x":1,"x":2}',
        '{"x":1e999}',
        None,
    ],
)
def test_invalid_json(payload):
    with pytest.raises(InvalidDelivery):
        load_delivery("id", "source", payload)


class Cursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class Connection:
    def __init__(self, rows):
        self.rows = rows
        self.done = {}

    @asynccontextmanager
    async def transaction(self):
        original = copy.deepcopy(self.done)
        try:
            yield
        except BaseException:
            self.done = original
            raise

    async def execute(self, sql, params):
        assert "manifest" not in sql
        if sql.startswith("SELECT"):
            assert "FOR UPDATE OF e SKIP LOCKED" in sql
            return Cursor(
                next(
                    (
                        row
                        for row in self.rows
                        if row[0] not in self.done and row[1] in params[0]
                    ),
                    None,
                )
            )
        self.done[params[-2]] = "rejected" if "rejected_at" in sql else "accepted"
        return Cursor(None)


@pytest.mark.asyncio
async def test_single_rows_source_scoped_and_acknowledged_after_callback():
    connection = Connection(
        [
            ("foreign", "other", "{}"),
            ("one", "source", '{"invoice":42}'),
            ("bad", "source", "[]"),
        ]
    )
    received = []

    async def accept(delivery):
        assert delivery.id not in connection.done
        received.append(delivery.payload)

    await poll_deliveries(connection, {"source"}, accept)
    await poll_deliveries(connection, {"source"}, accept)
    assert received == [{"invoice": 42}]
    assert connection.done == {"one": "accepted", "bad": "rejected"}


@pytest.mark.asyncio
async def test_failed_intake_is_not_acknowledged():
    connection = Connection([("one", "source", "{}")])

    async def accept(delivery):
        raise OSError("unavailable")

    with pytest.raises(OSError):
        await poll_deliveries(connection, {"source"}, accept)
    assert connection.done == {}


@pytest.mark.asyncio
async def test_domain_rejection_does_not_block_next_row():
    connection = Connection([("one", "source", "{}"), ("two", "source", "{}")])

    async def accept(delivery):
        if delivery.id == "one":
            raise ValueError("domain mismatch")

    await poll_deliveries(connection, {"source"}, accept)
    assert connection.done == {"one": "rejected", "two": "accepted"}
