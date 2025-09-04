package main

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

// RPCClient handles JSON-RPC communication with the Python server
type RPCClient struct {
	conn       *websocket.Conn
	url        string
	token      string
	nextID     int32
	pending    map[string]chan json.RawMessage
	mu         sync.RWMutex
	handlers   map[string]func(json.RawMessage)
	connected  bool
	closeChan  chan struct{}
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
				c.connected = false
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

	// Create response channel
	respChan := make(chan json.RawMessage, 1)
	c.mu.Lock()
	c.pending[idStr] = respChan
	c.mu.Unlock()

	// Send request
	if err := c.conn.WriteMessage(websocket.TextMessage, data); err != nil {
		c.mu.Lock()
		delete(c.pending, idStr)
		c.mu.Unlock()
		return nil, err
	}

	// Wait for response
	result := <-respChan
	return result, nil
}

// RegisterHandler registers a handler for server notifications
func (c *RPCClient) RegisterHandler(method string, handler func(json.RawMessage)) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.handlers[method] = handler
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
	result, err := c.call("list_sessions", nil)
	if err != nil {
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

// Response types

type RunResponse struct {
	RunID     string `json:"run_id"`
	SessionID string `json:"session_id"`
}

type Session struct {
	ID           string `json:"id"`
	CreatedAt    string `json:"created_at"`
	Archived     bool   `json:"archived"`
	MessageCount int    `json:"message_count"`
}