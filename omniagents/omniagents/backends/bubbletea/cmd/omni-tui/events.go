package main

import (
	"encoding/json"
)

// Event types for server notifications

type ToolCalledEvent struct {
	Tool   string `json:"tool"`
	Input  string `json:"input"`
	CallID string `json:"call_id"`
}

type ToolResultEvent struct {
	Tool     string                 `json:"tool"`
	Output   string                 `json:"output"`
	CallID   string                 `json:"call_id"`
	Metadata map[string]interface{} `json:"metadata,omitempty"`
}

type MessageOutputEvent struct {
	Content string `json:"content"`
}

type ToolApprovalRequestEvent struct {
	ApprovalID string                 `json:"approval_id"`
	Tool       string                 `json:"tool"`
	Arguments  string                 `json:"arguments"`
	Metadata   map[string]interface{} `json:"metadata,omitempty"`
}

type RunEndEvent struct {
	RunID     string `json:"run_id"`
	EndReason string `json:"end_reason"`
}

// Tea Msg types for Bubble Tea updates

type ToolCalledMsg struct {
	ToolCalledEvent
}

type ToolResultMsg struct {
	ToolResultEvent
}

type MessageOutputMsg struct {
	MessageOutputEvent
}

type ToolApprovalRequestMsg struct {
	ToolApprovalRequestEvent
}

type RunEndMsg struct {
	RunEndEvent
}

type ErrorMsg struct {
	Error error
}

type ConnectedMsg struct{}

type DisconnectedMsg struct{}

type AttemptReconnectMsg struct{}

type SessionsListedMsg struct {
	Sessions []Session
	Error    error
}

type RunStartedMsg struct {
	RunID     string
	SessionID string
}

type RPCReadyMsg struct{}

// ParseEventHandlers creates event handlers that convert RPC events to Tea messages
func ParseEventHandlers(send func(interface{})) map[string]func(json.RawMessage) {
	return map[string]func(json.RawMessage){
		"tool_called": func(data json.RawMessage) {
			var event ToolCalledEvent
			if err := json.Unmarshal(data, &event); err == nil {
				send(ToolCalledMsg{event})
			}
		},
		"tool_result": func(data json.RawMessage) {
			var event ToolResultEvent
			if err := json.Unmarshal(data, &event); err == nil {
				send(ToolResultMsg{event})
			}
		},
		"message_output": func(data json.RawMessage) {
			var event MessageOutputEvent
			if err := json.Unmarshal(data, &event); err == nil {
				send(MessageOutputMsg{event})
			}
		},
		"tool_approval_request": func(data json.RawMessage) {
			var event ToolApprovalRequestEvent
			if err := json.Unmarshal(data, &event); err == nil {
				send(ToolApprovalRequestMsg{event})
			}
		},
		"run_end": func(data json.RawMessage) {
			var event RunEndEvent
			if err := json.Unmarshal(data, &event); err == nil {
				send(RunEndMsg{event})
			}
		},
	}
}