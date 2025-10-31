import json

from hypothesis import given, settings, strategies as st

from omniagents.rpc.agents._events import (
    MessageOutputItem,
    ToolCallItem,
    ToolCallOutputItem,
    map_notification_to_event,
    new_item_event,
)


identifier = st.text(
    alphabet=st.characters(
        min_codepoint=32, max_codepoint=126, blacklist_characters=["\\"]
    ),
    max_size=32,
)

bounded_examples = settings(max_examples=25)


@bounded_examples
@given(identifier, identifier, st.one_of(st.none(), identifier))
def test_tool_call_item_round_trip(name, arguments, call_id):
    item = ToolCallItem(name, arguments, call_id)
    event = new_item_event("tool_called", item)
    assert event.name == "tool_called"
    assert event.item.name == name
    assert event.item.arguments == arguments
    payload = item.to_input_item()
    assert payload["name"] == name
    assert payload["arguments"] == arguments
    if not call_id:
        assert item.call_id is None
        assert "call_id" not in payload
    else:
        assert item.call_id == call_id
        assert payload["call_id"] == call_id


@bounded_examples
@given(identifier, identifier, st.one_of(st.none(), identifier))
def test_tool_call_output_item_round_trip(name, output, call_id):
    item = ToolCallOutputItem(name, output, call_id)
    event = new_item_event("tool_output", item)
    assert event.name == "tool_output"
    assert event.item.name == name
    assert event.item.output == output
    payload = item.to_input_item()
    assert payload["name"] == name
    assert payload["output"] == output
    if not call_id:
        assert event.item.raw_item["call_id"] is None
        assert "call_id" not in payload
    else:
        assert event.item.raw_item["call_id"] == call_id
        assert payload["call_id"] == call_id


@bounded_examples
@given(identifier)
def test_message_output_item_input_mapping(content):
    item = MessageOutputItem(content)
    payload = item.to_input_item()
    assert payload["role"] == "assistant"
    assert isinstance(payload["content"], str)


@bounded_examples
@given(identifier, identifier, st.one_of(st.none(), identifier))
def test_map_tool_called_notification(tool, input_payload, call_id):
    payload = {"tool": tool, "input": input_payload, "call_id": call_id}
    event = map_notification_to_event("tool_called", payload)
    assert event and event.name == "tool_called"
    assert event.item.name == tool
    assert event.item.arguments == input_payload
    if not call_id:
        assert event.item.call_id is None
    else:
        assert event.item.call_id == call_id


@bounded_examples
@given(identifier, identifier, st.one_of(st.none(), identifier))
def test_map_tool_result_notification(tool, output_payload, call_id):
    payload = {"tool": tool, "output": output_payload, "call_id": call_id}
    event = map_notification_to_event("tool_result", payload)
    assert event and event.name == "tool_output"
    assert event.item.name == tool
    assert event.item.output == output_payload
    if not call_id:
        assert event.item.raw_item["call_id"] is None
    else:
        assert event.item.raw_item["call_id"] == call_id


@bounded_examples
@given(identifier)
def test_map_message_output_notification(content):
    event = map_notification_to_event("message_output", {"content": content})
    assert event and event.name == "message_output_created"
    assert isinstance(event.item.to_input_item()["content"], str)


@bounded_examples
@given(st.dictionaries(keys=identifier, values=identifier, max_size=4))
def test_map_token_notification(payload):
    event = map_notification_to_event("token", payload)
    assert event and event.name == "token_usage"
    assert event.item == payload


@bounded_examples
@given(st.dictionaries(keys=identifier, values=identifier, max_size=4))
def test_map_client_request_notification(payload):
    event = map_notification_to_event("client_request", payload)
    assert event and event.name == "client_request"
    assert event.item == payload


@bounded_examples
@given(st.dictionaries(keys=identifier, values=identifier, max_size=4))
def test_map_unknown_notification(payload):
    event = map_notification_to_event("unknown", payload)
    assert event is None
