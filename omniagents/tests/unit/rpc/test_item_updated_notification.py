"""``item_updated``: payload shape, and that it survives strict validation.

The strict check is not ceremony. ``JsonRpcEndpoint`` validates every
outgoing notification against the generated model and treats a failure as a
dead channel — so a payload that does not match its schema does not raise a
visible error, it silently stops the client receiving ANY further
notification on that socket, ``run_end`` included. This test is the guard
that turns that into a loud, fast failure.
"""

import asyncio
from types import SimpleNamespace

import pytest

from omniagents.core.agents.service import AgentService
from omniagents.core.conversation.model import Item, ItemKind, ItemStatus
from omniagents.rpc.generated.gui_v2 import SERVER_NOTIFICATION_MODELS
from omniagents.rpc.protocol_catalog import EXPERIMENTAL_NOTIFICATIONS


def _item(kind=ItemKind.PLAN, **overrides):
    defaults = dict(
        item_id="itm_abc",
        thread_id="sess-1",
        turn_id="run-1",
        seq=7,
        kind=kind,
        status=ItemStatus.STARTED,
        created_at=1.5,
        updated_at=2.5,
        revision=3,
        content={"plan_id": "itm_abc", "steps": [{"id": "1", "status": "pending"}]},
        long_lived=True,
    )
    defaults.update(overrides)
    return Item(**defaults)


class _Capture:
    def __init__(self):
        self.sent = []

    async def _broadcast_session(self, session, method, payload, **kwargs):
        self.sent.append((method, payload))
        return True


def _emit(item, session_id="sess-1"):
    capture = _Capture()
    session = SimpleNamespace(id=session_id)
    asyncio.run(AgentService.notify_item_updated(capture, session, item))
    assert capture.sent, "no notification was emitted"
    method, payload = capture.sent[0]
    assert method == "item_updated"
    return payload


def test_payload_carries_identity_lifecycle_and_content():
    payload = _emit(_item())
    assert payload == {
        "session_id": "sess-1",
        "thread_id": "sess-1",
        "item_id": "itm_abc",
        "turn_id": "run-1",
        "kind": "plan",
        "status": "started",
        "revision": 3,
        "item_seq": 7,
        "updated_at": 2.5,
        "content": {
            "plan_id": "itm_abc",
            "steps": [{"id": "1", "status": "pending"}],
        },
    }


def test_item_ordinal_does_not_collide_with_the_replay_sequence():
    """``seq`` on a notification is the session's replay sequence id. The
    item's own thread ordinal therefore ships as ``item_seq``; naming both
    ``seq`` would have made one silently overwrite the other."""
    payload = _emit(_item(seq=42))
    assert payload["item_seq"] == 42
    assert "seq" not in payload
    model = SERVER_NOTIFICATION_MODELS["item_updated"]
    assert "seq" in model.model_fields
    assert "item_seq" in model.model_fields


@pytest.mark.parametrize(
    "status",
    [
        ItemStatus.STARTED,
        ItemStatus.COMPLETED,
        ItemStatus.FAILED,
        ItemStatus.CANCELLED,
    ],
)
def test_the_item_lifecycle_status_is_carried_verbatim(status):
    """``status`` is the only signal a client has that a plan or run diff
    has LANDED. Hard-coding it (or only ever testing an item that happens to
    be ``started``) leaves a live client showing "in progress" forever."""
    payload = _emit(_item(status=status))
    assert payload["status"] == status.value


def test_a_terminal_item_pushes_its_terminal_status():
    payload = _emit(
        _item(
            kind=ItemKind.RUN_DIFF,
            status=ItemStatus.COMPLETED,
            content={"diff": "d", "files": []},
        )
    )
    assert payload["status"] == "completed"
    assert payload["kind"] == "run_diff"


@pytest.mark.parametrize(
    "kind", [ItemKind.PLAN, ItemKind.RUN_DIFF, ItemKind.APPROVAL, ItemKind.ELICITATION]
)
def test_payload_validates_strictly_for_any_mutating_kind(kind):
    """Kind-agnostic by design: approvals and elicitations must be able to
    adopt this channel later without a protocol change."""
    payload = _emit(_item(kind=kind))
    model = SERVER_NOTIFICATION_MODELS["item_updated"]
    known = {k: v for k, v in payload.items() if k in model.model_fields}
    model.model_validate(known, strict=True)


def test_stamped_payload_validates_strictly():
    """The wire payload also carries the replay envelope the journal adds."""
    payload = {**_emit(_item()), "seq": 12, "stream_id": "stream-1"}
    model = SERVER_NOTIFICATION_MODELS["item_updated"]
    known = {k: v for k, v in payload.items() if k in model.model_fields}
    validated = model.model_validate(known, strict=True)
    assert validated.seq == 12
    assert validated.content["steps"][0]["id"] == "1"


def test_content_is_an_object_not_a_string_on_this_operation():
    """``content`` is message TEXT elsewhere on the protocol; here it is the
    item's open content record. The per-operation override is what keeps
    both true without re-typing a field on three stable methods."""
    model = SERVER_NOTIFICATION_MODELS["item_updated"]
    with pytest.raises(Exception):
        model.model_validate(
            {
                "session_id": "s",
                "thread_id": "s",
                "item_id": "i",
                "kind": "plan",
                "status": "started",
                "revision": 0,
                "item_seq": 1,
                "content": "not an object",
            },
            strict=True,
        )
    assert SERVER_NOTIFICATION_MODELS["message_output"].model_validate(
        {"run_id": "r", "session_id": "s", "content": "text"}, strict=True
    )


def test_a_broadcast_fault_never_propagates():
    """Plan/diff writes are already durable when the push happens; a dead
    socket must not turn into a failed tool call."""

    class Exploding:
        async def _broadcast_session(self, session, method, payload, **kwargs):
            raise RuntimeError("socket gone")

    asyncio.run(
        AgentService.notify_item_updated(
            Exploding(), SimpleNamespace(id="sess-1"), _item()
        )
    )


def test_item_updated_is_experimental_on_entry():
    assert "item_updated" in EXPERIMENTAL_NOTIFICATIONS
