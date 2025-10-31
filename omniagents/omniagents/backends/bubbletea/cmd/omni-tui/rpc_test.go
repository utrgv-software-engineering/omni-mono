package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

func TestRPCClientFlow(t *testing.T) {
	upgrader := websocket.Upgrader{CheckOrigin: func(r *http.Request) bool { return true }}
	var connMu sync.Mutex
	var wsConn *websocket.Conn
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/ws" {
			http.NotFound(w, r)
			return
		}
		c, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		connMu.Lock()
		wsConn = c
		connMu.Unlock()
		go func() {
			for {
				_, data, err := c.ReadMessage()
				if err != nil {
					return
				}
				var req map[string]interface{}
				if err := json.Unmarshal(data, &req); err != nil {
					continue
				}
				id := req["id"]
				method, _ := req["method"].(string)
				var result interface{}
				switch method {
				case "client_functions":
					result = true
				case "start_run":
					result = map[string]interface{}{"run_id": "run-1", "session_id": "sess-1"}
					event := map[string]interface{}{
						"jsonrpc": "2.0",
						"method":  "test_event",
						"params":  map[string]interface{}{"value": 7},
					}
					_ = c.WriteJSON(event)
				case "list_sessions":
					result = []map[string]interface{}{{"id": "sess-1", "message_count": 2}}
				case "list_server_functions":
					result = []map[string]interface{}{{"name": "echo", "description": "Echo"}}
				case "server_call":
					result = map[string]interface{}{"message": "ok"}
				case "client_response":
					result = true
				case "stop_run":
					result = true
				case "get_user_history":
					result = []map[string]interface{}{{"content": "hello"}}
				default:
					result = map[string]interface{}{}
				}
				resp := map[string]interface{}{"jsonrpc": "2.0", "id": id, "result": result}
				_ = c.WriteJSON(resp)
			}
		}()
	}))
	defer server.Close()

	url := "ws" + server.URL[len("http"):]
	url += "/ws"

	rpc := NewRPCClient(url, "")
	ready := make(chan struct{})
	rpc.SetOnReady(func() { close(ready) })

	disconnect := make(chan error, 1)
	rpc.SetOnDisconnect(func(err error) { disconnect <- err })

	event := make(chan struct{})
	rpc.RegisterHandler("test_event", func(raw json.RawMessage) {
		close(event)
	})

	if err := rpc.Connect(); err != nil {
		t.Fatalf("connect failed: %v", err)
	}

	select {
	case <-ready:
	case <-time.After(2 * time.Second):
		t.Fatalf("ready not signaled")
	}

	resp, err := rpc.StartRun("hello", nil, nil)
	if err != nil {
		t.Fatalf("start run failed: %v", err)
	}
	if resp.RunID != "run-1" {
		t.Fatalf("unexpected run id %s", resp.RunID)
	}

	select {
	case <-event:
	case <-time.After(2 * time.Second):
		t.Fatalf("event not received")
	}

	sessions, err := rpc.ListSessions()
	if err != nil || len(sessions) != 1 {
		t.Fatalf("unexpected sessions: %v %v", sessions, err)
	}

	funcs, err := rpc.ListServerFunctions()
	if err != nil || len(funcs) != 1 {
		t.Fatalf("unexpected functions: %v %v", funcs, err)
	}

	out, err := rpc.ServerCall("echo", map[string]interface{}{"text": "hi"}, nil)
	if err != nil || out["message"] != "ok" {
		t.Fatalf("unexpected server call result: %v %v", out, err)
	}

	if err := rpc.ClientResponse("req", true, map[string]interface{}{"ok": true}, nil); err != nil {
		t.Fatalf("client response failed: %v", err)
	}

	history, err := rpc.GetUserHistory(5, true)
	if err != nil || len(history) != 1 {
		t.Fatalf("unexpected history: %v %v", history, err)
	}

	if err := rpc.StopRun("run-1"); err != nil {
		t.Fatalf("stop run failed: %v", err)
	}

	connMu.Lock()
	if wsConn != nil {
		_ = wsConn.Close()
	}
	connMu.Unlock()

	select {
	case <-disconnect:
	case <-time.After(2 * time.Second):
		t.Fatalf("disconnect not observed")
	}

	if err := rpc.Close(); err != nil {
		t.Fatalf("close failed: %v", err)
	}
}
