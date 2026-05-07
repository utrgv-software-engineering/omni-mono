"""Shared event handling code for RPC agent implementations."""

from typing import Any, Dict
from agents import ItemHelpers, RunItemStreamEvent


def new_item_event(event_name: str, item: Any) -> RunItemStreamEvent:
    """Wrap item into an Agents RunItemStreamEvent with correct name."""
    return RunItemStreamEvent(event_name, item)


class ToolCallItem:
    """Represents a tool call for UI display."""

    def __init__(self, name: str, arguments: str, call_id: str | None = None):
        self.type = "tool_call_item"
        self.name = name
        self.arguments = arguments
        self.call_id = call_id or None

    def to_input_item(self):
        """Convert to input format for conversation history."""
        base = {
            "name": self.name,
            "arguments": self.arguments,
            "role": "assistant",
            "type": "tool_call",
        }
        if self.call_id:
            base["call_id"] = self.call_id
        return base


class ToolCallOutputItem:
    """Represents tool output for UI display, matching OpenAI SDK format."""

    def __init__(self, name: str, output: str, call_id: str | None = None):
        self.type = "tool_call_output_item"
        self.name = name
        self.output = output
        # Store call_id in raw_item to match OpenAI SDK format
        normalized_call_id = call_id or None
        self.raw_item = {
            "call_id": normalized_call_id,
            "output": output,
            "type": "function_call_output",
        }
        self.call_id = normalized_call_id

    def to_input_item(self):
        """Convert to input format for conversation history."""
        base = {
            "name": self.name,
            "output": self.output,
            "role": "assistant",
            "type": "tool_call_output",
        }
        if self.call_id:
            base["call_id"] = self.call_id
        return base


class MessageOutputItem:
    """Represents assistant message output."""

    def __init__(self, content: str):
        # Import here to avoid heavy cost at module load
        from openai.types.responses import ResponseOutputText, ResponseOutputMessage
        import uuid

        # Build minimal valid OpenAI response objects so ItemHelpers works
        text_part = ResponseOutputText(annotations=[], text=content, type="output_text")
        message = ResponseOutputMessage(
            id=uuid.uuid4().hex,
            content=[text_part],
            role="assistant",
            status="completed",
            type="message",
        )

        self.type = "message_output_item"
        self.raw_item = message  # Field expected by ItemHelpers

    def to_input_item(self):
        """Convert to model input format (simple assistant message)."""
        return {
            "role": "assistant",
            "content": ItemHelpers.extract_last_text(self.raw_item) or "",
        }


def map_notification_to_event(method: str, payload: Dict[str, Any]):
    """Map JSON-RPC notification to StreamEvent."""
    if method == "tool_called":
        item = ToolCallItem(
            payload.get("tool"), payload.get("input", "{}"), payload.get("call_id")
        )
        return new_item_event("tool_called", item)

    elif method == "tool_result":
        item = ToolCallOutputItem(
            payload.get("tool"), payload.get("output", ""), payload.get("call_id")
        )
        return new_item_event("tool_output", item)

    elif method == "message_output":
        item = MessageOutputItem(payload.get("content", ""))
        return new_item_event("message_output_created", item)

    elif method == "token":
        # Wrap usage (token) update as a stream event
        # Consumers can check event.name == "token_usage" and inspect event.item (payload dict)
        return new_item_event("token_usage", payload)

    elif method == "client_request":
        # Generic client-side function invocation from server.
        # Payload is passed-through; UI layer should inspect and act.
        return new_item_event("client_request", payload)

    elif method == "tool_approval_requested":
        # Tool approval prompt — UI layer renders the prompt and answers
        # via the tool_approval_response RPC method.
        return new_item_event("tool_approval_requested", payload)

    elif method == "tool_approval_resolved":
        # A pending approval was resolved (likely by another channel).
        # UI should dismiss any visible prompt for the matching call_id.
        return new_item_event("tool_approval_resolved", payload)

    elif method == "mcp_approval_requested":
        # Hosted-MCP approval prompt (SDK 0.16+). UI renders the prompt
        # using ``server_label`` / ``tool_name`` and answers via the
        # ``mcp_approval_response`` RPC method, echoing ``request_id``.
        return new_item_event("mcp_approval_requested", payload)

    elif method == "mcp_approval_resolved":
        # A pending MCP approval was resolved (likely by another channel).
        # UI should dismiss any visible prompt for the matching request_id.
        return new_item_event("mcp_approval_resolved", payload)

    return None
