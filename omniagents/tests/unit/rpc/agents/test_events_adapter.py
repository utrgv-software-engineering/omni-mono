from omniagents.rpc.agents._events import (
    ToolCallItem,
    ToolCallOutputItem,
    MessageOutputItem,
    map_notification_to_event,
)


def test_tool_call_item_to_input():
    item = ToolCallItem("tool", "{}", "call-1")
    payload = item.to_input_item()
    assert payload["name"] == "tool"
    assert payload["call_id"] == "call-1"


def test_tool_call_output_item_to_input():
    item = ToolCallOutputItem("tool", "result", None)
    payload = item.to_input_item()
    assert payload["name"] == "tool"
    assert "call_id" not in payload


def test_message_output_item_to_input():
    item = MessageOutputItem("hello")
    payload = item.to_input_item()
    assert payload["role"] == "assistant"
    assert isinstance(payload["content"], str)


def test_map_notification_variants():
    event = map_notification_to_event(
        "tool_called", {"tool": "tool", "input": "{}", "call_id": "1"}
    )
    assert event.name == "tool_called"

    event = map_notification_to_event(
        "tool_result", {"tool": "tool", "output": "ok", "call_id": "1"}
    )
    assert event.name == "tool_output"

    event = map_notification_to_event("message_output", {"content": "hi"})
    assert event.name == "message_output_created"

    event = map_notification_to_event("token", {"usage": 2})
    assert event.name == "token_usage"

    event = map_notification_to_event("client_request", {"action": "ping"})
    assert event.name == "client_request"

    event = map_notification_to_event("unknown", {})
    assert event is None
