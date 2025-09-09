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
    RunID     string        `json:"run_id"`
    EndReason string        `json:"end_reason"`
    Usage     *UsageDetails `json:"usage,omitempty"`
    // Model metadata (optional)
    Model           string `json:"model,omitempty"`
    ContextWindow   int    `json:"context_window,omitempty"`
    MaxOutputTokens int    `json:"max_output_tokens,omitempty"`
    Truncation      string `json:"truncation,omitempty"`
    // Optional application error details when EndReason == "error"
    Error *RunEndError `json:"error,omitempty"`
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

// RunStatusEvent is an optional server notification that a transient
// error occurred and the server will retry the run internally.
// This lets the UI show a friendly status like
// "Error: Request timed out. (Retrying 1/3)" while keeping the run alive.
type RunStatusEvent struct {
    RunID     string  `json:"run_id"`
    Status    string  `json:"status"`      // e.g., "retrying"
    Message   string  `json:"message"`     // human-readable error
    Attempt   int     `json:"attempt"`     // 1-based attempt number being retried
    Total     int     `json:"total"`       // total retry attempts
    NextDelay float64 `json:"next_delay,omitempty"` // backoff seconds
}

type RunStatusMsg struct {
    RunStatusEvent
}
// RunEndError carries application-level error details for failed runs
type RunEndError struct {
    Message string `json:"message"`
    Type    string `json:"type"`
}

// UsageDetails captures token/cost metrics sent by the server
type UsageDetails struct {
	Requests            int            `json:"requests,omitempty"`
	InputTokens         int            `json:"input_tokens,omitempty"`
	OutputTokens        int            `json:"output_tokens,omitempty"`
	TotalTokens         int            `json:"total_tokens,omitempty"`
	InputTokensDetails  map[string]int `json:"input_tokens_details,omitempty"`
	OutputTokensDetails map[string]int `json:"output_tokens_details,omitempty"`
}

// TokenUsageEvent is streamed after each model turn (method: "token")
type TokenUsageEvent struct {
	RunID      string `json:"run_id"`
	Turn       int    `json:"turn"`
	ResponseID string `json:"response_id"`
	// Model metadata (optional)
	Model           string       `json:"model,omitempty"`
	ContextWindow   int          `json:"context_window,omitempty"`
	MaxOutputTokens int          `json:"max_output_tokens,omitempty"`
	Truncation      string       `json:"truncation,omitempty"`
	Delta           UsageDetails `json:"delta"`
	Totals          UsageDetails `json:"totals"`
}

type TokenUsageMsg struct {
	TokenUsageEvent
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
        // Optional token usage streaming events
        "token": func(data json.RawMessage) {
            var event TokenUsageEvent
            if err := json.Unmarshal(data, &event); err == nil {
                send(TokenUsageMsg{event})
            }
        },
        // Optional status updates (e.g., retrying) from server
        "run_status": func(data json.RawMessage) {
            var event RunStatusEvent
            if err := json.Unmarshal(data, &event); err == nil {
                send(RunStatusMsg{event})
            }
        },
    }
}
