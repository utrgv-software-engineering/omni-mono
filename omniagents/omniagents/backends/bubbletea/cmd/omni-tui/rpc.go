package main

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

// RPCClient handles JSON-RPC communication with the Python server
type RPCClient struct {
    conn      *websocket.Conn
    url       string
    token     string
    nextID    int32
    pending   map[string]chan json.RawMessage
    mu        sync.RWMutex
    handlers  map[string]func(json.RawMessage)
    connected bool
    closeChan chan struct{}
    onReady   func() // Callback when receiveLoop is ready
    // onDisconnect is called exactly once when the receiveLoop exits due to a
    // connection error or normal close. Use this to notify the UI so it can
    // update state without freezing the interface.
    onDisconnect func(error)
}

// NewRPCClient creates a new RPC client
func NewRPCClient(serverURL string, token string) *RPCClient {
	return &RPCClient{
		url:       serverURL,
		token:     token,
		pending:   make(map[string]chan json.RawMessage),
		handlers:  make(map[string]func(json.RawMessage)),
		closeChan: make(chan struct{}),
	}
}

// Connect establishes the WebSocket connection
func (c *RPCClient) Connect() error {
	// Add token to URL if provided
	u, err := url.Parse(c.url)
	if err != nil {
		return fmt.Errorf("invalid URL: %w", err)
	}

	if c.token != "" {
		q := u.Query()
		q.Set("token", c.token)
		u.RawQuery = q.Encode()
	}

	// Debug output
	debug := os.Getenv("OMNI_DEBUG") == "1"
	if debug {
		fmt.Fprintf(os.Stderr, "[DEBUG] Attempting connection to: %s\n", u.String())
		fmt.Fprintf(os.Stderr, "[DEBUG] Host: %s, Path: %s\n", u.Host, u.Path)
		if c.token != "" {
			fmt.Fprintf(os.Stderr, "[DEBUG] Using authentication token\n")
		}
	}

	// Connect to WebSocket with timeout
	dialer := websocket.DefaultDialer
	dialer.HandshakeTimeout = 10 * time.Second

	if debug {
		fmt.Fprintf(os.Stderr, "[DEBUG] Starting WebSocket handshake...\n")
	}

	conn, resp, err := dialer.Dial(u.String(), nil)
	if err != nil {
		if debug && resp != nil {
			fmt.Fprintf(os.Stderr, "[DEBUG] Connection failed with status: %d\n", resp.StatusCode)
		}
		return fmt.Errorf("failed to connect to %s: %w", u.String(), err)
	}

	if debug {
		fmt.Fprintf(os.Stderr, "[DEBUG] WebSocket connection established successfully\n")
		if resp != nil {
			fmt.Fprintf(os.Stderr, "[DEBUG] Response status: %d\n", resp.StatusCode)
		}
	}

	c.conn = conn
	c.connected = true

	// Start receive loop
	go c.receiveLoop()

	if debug {
		fmt.Fprintf(os.Stderr, "[DEBUG] Receive loop started\n")
	}

	return nil
}

// Close closes the WebSocket connection
func (c *RPCClient) Close() error {
	c.connected = false
	close(c.closeChan)
	if c.conn != nil {
		return c.conn.Close()
	}
	return nil
}

// receiveLoop handles incoming messages from the server
func (c *RPCClient) receiveLoop() {
    // Small delay to ensure goroutine is scheduled and ready
    time.Sleep(10 * time.Millisecond)

    // Signal that the receive loop is ready
    if c.onReady != nil {
        c.onReady()
    }

	for {
		select {
		case <-c.closeChan:
			return
		default:
			if !c.connected || c.conn == nil {
				return
			}

            _, message, err := c.conn.ReadMessage()
            if err != nil {
                // Mark as disconnected and inform listeners so the UI can
                // remain interactive and show a non-blocking status.
                c.connected = false
                if c.onDisconnect != nil {
                    // Call in a separate goroutine to avoid blocking the
                    // receiveLoop on UI processing.
                    go c.onDisconnect(err)
                }
                return
            }

			var msg map[string]json.RawMessage
			if err := json.Unmarshal(message, &msg); err != nil {
				continue
			}

			// Check if it's a response to a request
			if id, hasID := msg["id"]; hasID {
				c.mu.RLock()
				ch, exists := c.pending[string(id)]
				c.mu.RUnlock()

				if exists {
					if result, hasResult := msg["result"]; hasResult {
						ch <- result
					} else if errMsg, hasError := msg["error"]; hasError {
						ch <- errMsg
					}
					close(ch)

					c.mu.Lock()
					delete(c.pending, string(id))
					c.mu.Unlock()
				}
			}

			// Check if it's a notification (server event)
			if method, hasMethod := msg["method"]; hasMethod {
				methodStr := ""
				json.Unmarshal(method, &methodStr)

				c.mu.RLock()
				handler, exists := c.handlers[methodStr]
				c.mu.RUnlock()

				if exists && handler != nil {
					params := msg["params"]
					handler(params)
				}
			}
		}
	}
}

// call makes an RPC call and waits for the response
func (c *RPCClient) call(method string, params interface{}) (json.RawMessage, error) {
	debug := os.Getenv("OMNI_DEBUG") == "1"

	if !c.connected {
		return nil, fmt.Errorf("not connected")
	}

	id := atomic.AddInt32(&c.nextID, 1)
	idStr := fmt.Sprintf("%d", id)

	request := map[string]interface{}{
		"jsonrpc": "2.0",
		"id":      id,
		"method":  method,
		"params":  params,
	}

	data, err := json.Marshal(request)
	if err != nil {
		return nil, err
	}

	if debug {
		fmt.Fprintf(os.Stderr, "[DEBUG] RPC call: sending request: %s\n", string(data))
	}

	// Create response channel
	respChan := make(chan json.RawMessage, 1)
	c.mu.Lock()
	c.pending[idStr] = respChan
	c.mu.Unlock()

	// Send request
	if err := c.conn.WriteMessage(websocket.TextMessage, data); err != nil {
		if debug {
			fmt.Fprintf(os.Stderr, "[DEBUG] RPC call: WriteMessage failed: %v\n", err)
		}
		c.mu.Lock()
		delete(c.pending, idStr)
		c.mu.Unlock()
		return nil, err
	}

	if debug {
		fmt.Fprintf(os.Stderr, "[DEBUG] RPC call: waiting for response on channel for id %s\n", idStr)
	}

    // Wait for response with timeout to avoid hanging forever if server doesn't respond
    // Allow override via OMNI_RPC_TIMEOUT seconds; fall back to sensible defaults.
    timeout := 15 * time.Second
    if env := os.Getenv("OMNI_RPC_TIMEOUT"); env != "" {
        if v, err := time.ParseDuration(env); err == nil {
            timeout = v
        } else {
            // If not a duration, try to parse seconds as int
            if sec, perr := strconv.Atoi(env); perr == nil {
                timeout = time.Duration(sec) * time.Second
            }
        }
    }
    // Some methods are known to be very fast; keep a minimum sensible timeout
    if method == "stop_run" || method == "tool_approval_response" {
        if timeout > 10*time.Second {
            timeout = 10 * time.Second
        }
    }
    select {
    case result := <-respChan:
        if debug {
            fmt.Fprintf(os.Stderr, "[DEBUG] RPC call: received response for id %s\n", idStr)
        }
        return result, nil
    case <-time.After(timeout):
        if debug {
            fmt.Fprintf(os.Stderr, "[DEBUG] RPC call: timeout waiting for response for id %s (method=%s)\n", idStr, method)
        }
        // Clean up pending map to avoid leaks
        c.mu.Lock()
        delete(c.pending, idStr)
        c.mu.Unlock()
        return nil, fmt.Errorf("rpc call timeout for method '%s'", method)
    }
}

// RegisterHandler registers a handler for server notifications
func (c *RPCClient) RegisterHandler(method string, handler func(json.RawMessage)) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.handlers[method] = handler
}

// SetOnReady sets the callback to be called when receiveLoop is ready
func (c *RPCClient) SetOnReady(callback func()) {
    c.onReady = callback
}

// SetOnDisconnect sets a callback that fires when the WebSocket connection
// is closed or a read error occurs. The error argument may be nil on normal
// closes. This allows the UI to remain responsive and update status instead
// of appearing to hang after a server-side error.
func (c *RPCClient) SetOnDisconnect(callback func(error)) {
    c.onDisconnect = callback
}

// RPC method implementations

// StartRun starts a new agent run
func (c *RPCClient) StartRun(prompt string, sessionID *string, variables map[string]interface{}) (*RunResponse, error) {
	params := map[string]interface{}{
		"prompt": prompt,
	}
	if sessionID != nil {
		params["session_id"] = *sessionID
	}
	if variables != nil {
		params["variables"] = variables
	}

	result, err := c.call("start_run", params)
	if err != nil {
		return nil, err
	}

    var response RunResponse
    if err := json.Unmarshal(result, &response); err != nil {
        // Try to parse JSON-RPC error object (receiveLoop passes the error payload directly)
        var rpcErr struct {
            Code    int             `json:"code"`
            Message string          `json:"message"`
            Data    json.RawMessage `json:"data,omitempty"`
        }
        if perr := json.Unmarshal(result, &rpcErr); perr == nil && rpcErr.Message != "" {
            return nil, fmt.Errorf("start_run failed: %s", rpcErr.Message)
        }
        return nil, err
    }

	return &response, nil
}

// StopRun stops an active run
func (c *RPCClient) StopRun(runID string) error {
	params := map[string]interface{}{
		"run_id": runID,
	}

	_, err := c.call("stop_run", params)
	return err
}

// ListSessions returns all available sessions
func (c *RPCClient) ListSessions() ([]Session, error) {
	debug := os.Getenv("OMNI_DEBUG") == "1"
	if debug {
		fmt.Fprintf(os.Stderr, "[DEBUG] ListSessions: calling list_sessions RPC\n")
	}

	result, err := c.call("list_sessions", nil)
	if err != nil {
		if debug {
			fmt.Fprintf(os.Stderr, "[DEBUG] ListSessions: error from call: %v\n", err)
		}
		return nil, err
	}

	var sessions []Session
	if err := json.Unmarshal(result, &sessions); err != nil {
		return nil, err
	}

	return sessions, nil
}

// ArchiveSession archives a session
func (c *RPCClient) ArchiveSession(sessionID string) error {
    params := map[string]interface{}{
        "session_id": sessionID,
    }

    _, err := c.call("archive_session", params)
    return err
}

// ToolApprovalResponse responds to a tool approval request
func (c *RPCClient) ToolApprovalResponse(approvalID string, approved bool, alwaysApprove bool) error {
    params := map[string]interface{}{
        "approval_id":    approvalID,
        "approved":       approved,
        "always_approve": alwaysApprove,
    }

    _, err := c.call("tool_approval_response", params)
    return err
}

// ClientFunctions advertises client capabilities to the server (optional)
func (c *RPCClient) ClientFunctions(version int, functions []map[string]interface{}) error {
    params := map[string]interface{}{
        "version":   version,
        "functions": functions,
    }
    _, err := c.call("client_functions", params)
    return err
}

// ClientResponse replies to a client_request issued by the server
func (c *RPCClient) ClientResponse(requestID string, ok bool, result map[string]interface{}, errObj map[string]interface{}) error {
    params := map[string]interface{}{
        "request_id": requestID,
        "ok":         ok,
    }
    if result != nil {
        params["result"] = result
    }
    if errObj != nil {
        params["error"] = errObj
    }
    _, err := c.call("client_response", params)
    return err
}

// Response types

type RunResponse struct {
	RunID     string `json:"run_id"`
	SessionID string `json:"session_id"`
}

type SessionMessage struct {
	Role      string      `json:"role"`
	Content   interface{} `json:"content"` // Can be string or array
	Timestamp string      `json:"timestamp"`
}

// GetContentText extracts text content from a SessionMessage
func (m *SessionMessage) GetContentText() string {
	if m == nil || m.Content == nil {
		return ""
	}

	// Handle string content
	if str, ok := m.Content.(string); ok {
		return str
	}

	// Handle array content (OpenAI format)
	if arr, ok := m.Content.([]interface{}); ok {
		for _, item := range arr {
			if contentPart, ok := item.(map[string]interface{}); ok {
				if contentPart["type"] == "output_text" {
					if text, ok := contentPart["text"].(string); ok {
						return text
					}
				}
			}
		}
	}

	return ""
}

type Session struct {
    ID           string          `json:"id"`
    CreatedAt    string          `json:"created_at"`
    Archived     bool            `json:"archived"`
    MessageCount int             `json:"message_count"`
    FirstMessage *SessionMessage `json:"first_message,omitempty"`
    LastMessage  *SessionMessage `json:"last_message,omitempty"`
}

// Server functions RPC -------------------------------------------------

type ServerFunctionMeta struct {
    Name        string                 `json:"name"`
    Description string                 `json:"description,omitempty"`
    // Optional schemas omitted for simplicity
    ParamsSchema map[string]interface{} `json:"params_schema,omitempty"`
    ResultSchema map[string]interface{} `json:"result_schema,omitempty"`
}

func (c *RPCClient) ListServerFunctions() ([]ServerFunctionMeta, error) {
    result, err := c.call("list_server_functions", map[string]interface{}{})
    if err != nil {
        return nil, err
    }
    var funcs []ServerFunctionMeta
    if err := json.Unmarshal(result, &funcs); err != nil {
        return nil, err
    }
    return funcs, nil
}

func (c *RPCClient) ServerCall(function string, args map[string]interface{}, sessionID *string) (map[string]interface{}, error) {
    params := map[string]interface{}{
        "function": function,
    }
    if args != nil {
        params["args"] = args
    }
    if sessionID != nil {
        params["session_id"] = *sessionID
    }
    raw, err := c.call("server_call", params)
    if err != nil {
        return nil, err
    }
    var out map[string]interface{}
    if err := json.Unmarshal(raw, &out); err != nil {
        return nil, err
    }
    return out, nil
}

// History RPCs -------------------------------------------------------

func (c *RPCClient) GetSessionHistory(sessionID string) ([]SessionMessage, error) {
    params := map[string]interface{}{
        "session_id": sessionID,
    }
    raw, err := c.call("get_session_history", params)
    if err != nil {
        return nil, err
    }
    var msgs []SessionMessage
    if err := json.Unmarshal(raw, &msgs); err != nil {
        return nil, err
    }
    return msgs, nil
}

type UserHistoryItem struct {
    Content   string `json:"content"`
    SessionID string `json:"session_id,omitempty"`
    Timestamp string `json:"timestamp,omitempty"`
}

func (c *RPCClient) GetUserHistory(limit int, includeArchived bool) ([]UserHistoryItem, error) {
    params := map[string]interface{}{
        "limit":             limit,
        "include_archived":  includeArchived,
    }
    raw, err := c.call("get_user_history", params)
    if err != nil {
        return nil, err
    }
    var items []UserHistoryItem
    if err := json.Unmarshal(raw, &items); err != nil {
        return nil, err
    }
    return items, nil
}
