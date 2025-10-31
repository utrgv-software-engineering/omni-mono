package main

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/bubbles/spinner"
	tea "github.com/charmbracelet/bubbletea"
)

func unwrapModel(t *testing.T, tm tea.Model) Model {
	model, ok := tm.(Model)
	if !ok {
		t.Fatalf("expected Model, got %T", tm)
	}
	return model
}

func ensureTheme() {
	LoadDefaultTheme()
	initStyles()
	initApprovalStyles()
}

type clientResponseCall struct {
	requestID string
	ok        bool
	result    map[string]interface{}
	err       map[string]interface{}
}

type serverCallRecord struct {
	name       string
	args       map[string]interface{}
	sessionID  string
	hasSession bool
}

type stubRPC struct {
	listFuncs        []ServerFunctionMeta
	listErr          error
	serverCallFn     func(string, map[string]interface{}, *string) (map[string]interface{}, error)
	startRunFn       func(string, *string, map[string]interface{}) (*RunResponse, error)
	clientRespErr    error
	listSessionsResp []Session
	listSessionsErr  error
	stopRunErr       error
	userHistoryResp  []UserHistoryItem
	userHistoryErr   error
	clientResponses  []clientResponseCall
	serverCalls      []serverCallRecord
	stopRunCalls     []string
	startRunCalls    []struct {
		prompt    string
		sessionID string
		hasSess   bool
	}
}

func (s *stubRPC) RegisterHandler(string, func(json.RawMessage)) {}

func (s *stubRPC) SetOnReady(func()) {}

func (s *stubRPC) ClientFunctions(int, []map[string]interface{}) error { return nil }

func (s *stubRPC) SetOnDisconnect(func(error)) {}

func (s *stubRPC) Connect() error { return nil }

func (s *stubRPC) StartRun(prompt string, sessionID *string, variables map[string]interface{}) (*RunResponse, error) {
	call := struct {
		prompt    string
		sessionID string
		hasSess   bool
	}{prompt: prompt}
	if sessionID != nil {
		call.sessionID = *sessionID
		call.hasSess = true
	}
	s.startRunCalls = append(s.startRunCalls, call)
	if s.startRunFn != nil {
		return s.startRunFn(prompt, sessionID, variables)
	}
	return nil, nil
}

func (s *stubRPC) ClientResponse(requestID string, ok bool, result map[string]interface{}, errObj map[string]interface{}) error {
	call := clientResponseCall{requestID: requestID, ok: ok}
	if result != nil {
		call.result = make(map[string]interface{})
		for k, v := range result {
			call.result[k] = v
		}
	}
	if errObj != nil {
		call.err = make(map[string]interface{})
		for k, v := range errObj {
			call.err[k] = v
		}
	}
	s.clientResponses = append(s.clientResponses, call)
	return s.clientRespErr
}

func (s *stubRPC) ListServerFunctions() ([]ServerFunctionMeta, error) {
	if s.listErr != nil {
		return nil, s.listErr
	}
	return s.listFuncs, nil
}

func (s *stubRPC) ServerCall(name string, args map[string]interface{}, sessionID *string) (map[string]interface{}, error) {
	rec := serverCallRecord{name: name}
	if args != nil {
		rec.args = make(map[string]interface{})
		for k, v := range args {
			rec.args[k] = v
		}
	}
	if sessionID != nil {
		rec.sessionID = *sessionID
		rec.hasSession = true
	}
	s.serverCalls = append(s.serverCalls, rec)
	if s.serverCallFn != nil {
		return s.serverCallFn(name, args, sessionID)
	}
	return map[string]interface{}{}, nil
}

func (s *stubRPC) ListSessions() ([]Session, error) {
	if s.listSessionsErr != nil {
		return nil, s.listSessionsErr
	}
	return s.listSessionsResp, nil
}

func (s *stubRPC) StopRun(id string) error {
	s.stopRunCalls = append(s.stopRunCalls, id)
	return s.stopRunErr
}

func (s *stubRPC) GetUserHistory(limit int, includeArchived bool) ([]UserHistoryItem, error) {
	if s.userHistoryErr != nil {
		return nil, s.userHistoryErr
	}
	return s.userHistoryResp, nil
}

func TestParseEventHandlersRoutesEvents(t *testing.T) {
	ensureTheme()
	var received []interface{}
	handlers := ParseEventHandlers(func(msg interface{}) {
		received = append(received, msg)
	})

	toolPayload, err := json.Marshal(ToolCalledEvent{Tool: "shell", Input: "{}", CallID: "abc"})
	if err != nil {
		t.Fatalf("marshal tool event: %v", err)
	}
	handlers["tool_called"](toolPayload)
	if len(received) != 1 {
		t.Fatalf("expected 1 message, got %d", len(received))
	}
	if _, ok := received[0].(ToolCalledMsg); !ok {
		t.Fatalf("expected ToolCalledMsg, got %T", received[0])
	}

	runPayload, err := json.Marshal(RunEndEvent{RunID: "run", EndReason: "success"})
	if err != nil {
		t.Fatalf("marshal run event: %v", err)
	}
	handlers["run_end"](runPayload)
	if len(received) != 2 {
		t.Fatalf("expected 2 messages, got %d", len(received))
	}
	if _, ok := received[1].(RunEndMsg); !ok {
		t.Fatalf("expected RunEndMsg, got %T", received[1])
	}

	messagePayload, err := json.Marshal(MessageOutputEvent{Content: "hi"})
	if err != nil {
		t.Fatalf("marshal message event: %v", err)
	}
	handlers["message_output"](messagePayload)
	if _, ok := received[2].(MessageOutputMsg); !ok {
		t.Fatalf("expected MessageOutputMsg")
	}

	statusPayload, err := json.Marshal(RunStatusEvent{RunID: "rid", Message: "timeout", Attempt: 1, Total: 2})
	if err != nil {
		t.Fatalf("marshal status event: %v", err)
	}
	handlers["run_status"](statusPayload)
	if _, ok := received[3].(RunStatusMsg); !ok {
		t.Fatalf("expected RunStatusMsg")
	}

	tokenPayload, err := json.Marshal(TokenUsageEvent{RunID: "rid", Totals: UsageDetails{TotalTokens: 3}})
	if err != nil {
		t.Fatalf("marshal token event: %v", err)
	}
	handlers["token"](tokenPayload)
	if _, ok := received[4].(TokenUsageMsg); !ok {
		t.Fatalf("expected TokenUsageMsg")
	}

	startedPayload, err := json.Marshal(RunStartedEvent{RunID: "rid", SessionID: "sid"})
	if err != nil {
		t.Fatalf("marshal start event: %v", err)
	}
	handlers["run_started"](startedPayload)
	if _, ok := received[5].(RunStartedMsg); !ok {
		t.Fatalf("expected RunStartedMsg")
	}

	approvalPayload := ClientRequestEvent{
		RequestID: "req",
		Function:  "ui.request_tool_approval",
		Args: map[string]interface{}{
			"tool":      "shell",
			"arguments": "{}",
			"metadata":  map[string]interface{}{"display_type": "command"},
		},
	}
	approvalRaw, err := json.Marshal(approvalPayload)
	if err != nil {
		t.Fatalf("marshal approval event: %v", err)
	}
	handlers["client_request"](json.RawMessage(approvalRaw))
	if _, ok := received[6].(ToolApprovalRequestMsg); !ok {
		t.Fatalf("expected ToolApprovalRequestMsg")
	}

	clientPayload := ClientRequestEvent{RequestID: "req2", Function: "ui.set_status", Args: map[string]interface{}{"text": "hi"}}
	clientRaw, err := json.Marshal(clientPayload)
	if err != nil {
		t.Fatalf("marshal client event: %v", err)
	}
	handlers["client_request"](json.RawMessage(clientRaw))
	if _, ok := received[7].(ClientRequestMsg); !ok {
		t.Fatalf("expected ClientRequestMsg")
	}
}

func TestHandleSlashCommandHelp(t *testing.T) {
	ensureTheme()
	rpc := &stubRPC{
		listFuncs: []ServerFunctionMeta{
			{
				Name:        "ping",
				Description: "Ping",
				ParamsSchema: map[string]interface{}{
					"properties": map[string]interface{}{
						"text": map[string]interface{}{},
					},
				},
			},
		},
	}
	model := NewModel(nil, "Agent", "Welcome", false, "", false, "")
	model.rpcClient = rpc
	msg := model.handleSlashCommand("/help")()
	res, ok := msg.(CommandResultMsg)
	if !ok {
		t.Fatalf("expected CommandResultMsg, got %T", msg)
	}
	if !strings.Contains(res.Content, "/ping <text>") {
		t.Fatalf("expected help content, got %q", res.Content)
	}
}

func TestHandleSlashCommandFallback(t *testing.T) {
	ensureTheme()
	rpc := &stubRPC{}
	model := NewModel(nil, "Agent", "Welcome", false, "", false, "")
	model.rpcClient = rpc
	msg := model.handleSlashCommand("/unknown data")()
	res, ok := msg.(FallbackToLLMMsg)
	if !ok {
		t.Fatalf("expected FallbackToLLMMsg, got %T", msg)
	}
	if res.Text != "/unknown data" {
		t.Fatalf("unexpected fallback text %q", res.Text)
	}
}

func TestHandleSlashCommandServerCall(t *testing.T) {
	ensureTheme()
	rpc := &stubRPC{
		listFuncs: []ServerFunctionMeta{{Name: "echo"}},
		serverCallFn: func(name string, args map[string]interface{}, sessionID *string) (map[string]interface{}, error) {
			if name != "echo" {
				t.Fatalf("unexpected function %s", name)
			}
			if args["text"] != "hi" {
				t.Fatalf("unexpected args %v", args)
			}
			if sessionID == nil || *sessionID != "sess" {
				t.Fatalf("expected session id")
			}
			return map[string]interface{}{"message": "ok"}, nil
		},
	}
	model := NewModel(nil, "Agent", "Welcome", false, "sess", false, "")
	model.rpcClient = rpc
	msg := model.handleSlashCommand(`/echo {"text":"hi"}`)()
	res, ok := msg.(CommandResultMsg)
	if !ok {
		t.Fatalf("expected CommandResultMsg, got %T", msg)
	}
	if res.Content != "ok" {
		t.Fatalf("unexpected content %q", res.Content)
	}
	if len(rpc.serverCalls) != 1 {
		t.Fatalf("expected server call recorded")
	}
	call := rpc.serverCalls[0]
	if !call.hasSession || call.sessionID != "sess" {
		t.Fatalf("expected session captured")
	}
}

func TestHandleSlashCommandError(t *testing.T) {
	ensureTheme()
	rpc := &stubRPC{
		listFuncs: []ServerFunctionMeta{{Name: "fail"}},
		serverCallFn: func(string, map[string]interface{}, *string) (map[string]interface{}, error) {
			return nil, errors.New("boom")
		},
	}
	model := NewModel(nil, "Agent", "Welcome", false, "", false, "")
	model.rpcClient = rpc
	msg := model.handleSlashCommand("/fail")()
	_, ok := msg.(ErrorMsg)
	if !ok {
		t.Fatalf("expected ErrorMsg, got %T", msg)
	}
}

func TestStartRunVariants(t *testing.T) {
	ensureTheme()
	rpc := &stubRPC{
		startRunFn: func(prompt string, sessionID *string, variables map[string]interface{}) (*RunResponse, error) {
			if prompt != "hello" {
				t.Fatalf("unexpected prompt %s", prompt)
			}
			if sessionID == nil || *sessionID != "sess" {
				t.Fatalf("expected session id")
			}
			if variables != nil {
				t.Fatalf("expected nil variables")
			}
			return &RunResponse{RunID: "run", SessionID: "sess"}, nil
		},
	}
	model := NewModel(nil, "Agent", "Welcome", false, "sess", false, "")
	model.rpcClient = rpc
	cmd := model.startRun("hello")
	msg := cmd()
	started, ok := msg.(RunStartedMsg)
	if !ok {
		t.Fatalf("expected RunStartedMsg, got %T", msg)
	}
	if started.RunID != "run" || started.SessionID != "sess" {
		t.Fatalf("unexpected run data %+v", started)
	}

	rpcFail := &stubRPC{
		startRunFn: func(string, *string, map[string]interface{}) (*RunResponse, error) {
			return nil, errors.New("fail")
		},
	}
	modelFail := NewModel(rpcFail, "Agent", "Welcome", false, "", false, "")
	msgFail := modelFail.startRun("oops")()
	if _, ok := msgFail.(ErrorMsg); !ok {
		t.Fatalf("expected ErrorMsg, got %T", msgFail)
	}
}

func TestHandleApprovalListChoiceVariants(t *testing.T) {
	ensureTheme()
	rpc := &stubRPC{}
	model := NewModel(rpc, "Agent", "Welcome", false, "sess", false, "")
	model.height = 40
	model.pendingTool = &ToolApprovalRequestEvent{RequestID: "req"}
	model.messages = []Message{{Role: "approval_request", Approval: &ToolApprovalInfo{Tool: "shell", Arguments: "echo"}}}
	model.approvalList = CreateApprovalList("shell", "echo", 60)
	model.approvalList.Select(1)
	next, cmd := model.handleApprovalListChoice()
	if next.state != StateWaitingForResponse {
		t.Fatalf("unexpected state %v", next.state)
	}
	if cmd == nil {
		t.Fatalf("expected command")
	}
	msg := cmd()
	batch, ok := msg.(tea.BatchMsg)
	if !ok {
		t.Fatalf("expected BatchMsg, got %T", msg)
	}
	for _, inner := range batch {
		if inner == nil {
			continue
		}
		inner()
	}
	if len(rpc.clientResponses) != 1 {
		t.Fatalf("expected client response recorded")
	}
	resp := rpc.clientResponses[0]
	if !resp.ok || resp.result["always_approve"] != true {
		t.Fatalf("unexpected response %+v", resp)
	}

	model.pendingTool = &ToolApprovalRequestEvent{RequestID: "req2"}
	model.messages = []Message{{Role: "approval_request", Approval: &ToolApprovalInfo{Tool: "shell", Arguments: "echo"}}}
	model.approvalList = CreateApprovalList("shell", "echo", 60)
	model.approvalList.Select(2)
	_, denyCmd := model.handleApprovalListChoice()
	denyMsg := denyCmd()
	denyBatch, ok := denyMsg.(tea.BatchMsg)
	if !ok {
		t.Fatalf("expected BatchMsg")
	}
	for _, inner := range denyBatch {
		if inner == nil {
			continue
		}
		inner()
	}
	if len(rpc.clientResponses) != 2 {
		t.Fatalf("expected second response")
	}
	deny := rpc.clientResponses[1]
	approved, _ := deny.result["approved"].(bool)
	always, _ := deny.result["always_approve"].(bool)
	if approved || always {
		t.Fatalf("expected denial flags false")
	}
	if deny.result["error_message"] != "User rejected tool call" {
		t.Fatalf("unexpected error message")
	}
}

func TestListSessionsDispatch(t *testing.T) {
	ensureTheme()
	rpc := &stubRPC{listSessionsResp: []Session{{ID: "a"}, {ID: "b"}}}
	model := NewModel(rpc, "Agent", "Welcome", false, "", false, "")
	ch := make(chan tea.Msg, 2)
	model.msgChan = ch
	_, _ = model.listSessions()
	select {
	case msg := <-ch:
		listed, ok := msg.(SessionsListedMsg)
		if !ok {
			t.Fatalf("expected SessionsListedMsg, got %T", msg)
		}
		if len(listed.Sessions) != 2 {
			t.Fatalf("unexpected sessions count")
		}
	case <-time.After(2 * time.Second):
		t.Fatalf("timeout waiting for sessions")
	}

	rpcErr := &stubRPC{listSessionsErr: errors.New("fail")}
	modelErr := NewModel(rpcErr, "Agent", "Welcome", false, "", false, "")
	errCh := make(chan tea.Msg, 1)
	modelErr.msgChan = errCh
	_, _ = modelErr.listSessions()
	select {
	case msg := <-errCh:
		listed, ok := msg.(SessionsListedMsg)
		if !ok {
			t.Fatalf("expected SessionsListedMsg, got %T", msg)
		}
		if listed.Error == nil {
			t.Fatalf("expected error")
		}
	case <-time.After(2 * time.Second):
		t.Fatalf("timeout waiting for error")
	}
}

func TestMaybeSendInitialPrompt(t *testing.T) {
	ensureTheme()
	rpc := &stubRPC{
		startRunFn: func(prompt string, sessionID *string, variables map[string]interface{}) (*RunResponse, error) {
			if prompt != "hello" {
				t.Fatalf("unexpected prompt %s", prompt)
			}
			return &RunResponse{RunID: "run", SessionID: "sess"}, nil
		},
	}
	model := NewModel(rpc, "Agent", "Welcome", false, "sess", false, "hello")
	model.height = 40
	model.initialPromptPending = true
	updated, cmd := model.maybeSendInitialPrompt()
	if cmd == nil {
		t.Fatalf("expected send command")
	}
	if updated.initialPromptPending {
		t.Fatalf("expected flag cleared")
	}
	msg := cmd()
	started, ok := msg.(RunStartedMsg)
	if !ok {
		t.Fatalf("expected RunStartedMsg, got %T", msg)
	}
	if started.RunID != "run" {
		t.Fatalf("unexpected run id")
	}

	model2 := NewModel(nil, "Agent", "Welcome", false, "", false, "   ")
	model2.initialPromptPending = true
	next, nextCmd := model2.maybeSendInitialPrompt()
	if nextCmd != nil {
		t.Fatalf("expected nil command for empty prompt")
	}
	if next.input.Value() != "" {
		t.Fatalf("expected empty input")
	}
}

func TestTruncateMessageForList(t *testing.T) {
	ensureTheme()
	model := NewModel(nil, "Agent", "Welcome", false, "", false, "")
	single := model.truncateMessageForList("hello", 20)
	if single != "hello" {
		t.Fatalf("unexpected truncation %q", single)
	}
	multi := model.truncateMessageForList("\nline one\nline two", 10)
	if multi != "line one" {
		t.Fatalf("expected first meaningful line, got %q", multi)
	}
	truncated := model.truncateMessageForList(strings.Repeat("a", 50), 20)
	if !strings.HasSuffix(truncated, "...") {
		t.Fatalf("expected ellipsis")
	}
}

func TestRenderWelcome(t *testing.T) {
	ensureTheme()
	out := renderWelcome("hello world")
	if !strings.Contains(out, "hello world") {
		t.Fatalf("expected welcome text")
	}
	if renderWelcome("") != "" {
		t.Fatalf("expected empty string for blank input")
	}
}

func TestSelectSession(t *testing.T) {
	ensureTheme()
	model := NewModel(nil, "Agent", "", false, "", false, "")
	model.availableSessions = []Session{{ID: "s1"}, {ID: "s2"}}
	model.paginator.SetTotalPages(len(model.availableSessions))
	model.selectedSession = 1
	model.state = StateSessionList
	updated, cmd := model.selectSession()
	if updated.sessionID != "s2" {
		t.Fatalf("expected second session, got %s", updated.sessionID)
	}
	if updated.state != StateReady {
		t.Fatalf("expected ready state")
	}
	if cmd != nil {
		t.Fatalf("expected nil command")
	}

	model.selectedSession = 5
	_, cmd = model.selectSession()
	if cmd != nil {
		t.Fatalf("expected nil command for out of range selection")
	}
}

func TestListenForMessages(t *testing.T) {
	ensureTheme()
	model := NewModel(nil, "Agent", "", false, "", false, "")
	noChanCmd := model.listenForMessages()
	if msg := noChanCmd(); msg.(ChannelMsg).Msg != nil {
		t.Fatalf("expected nil message when channel missing")
	}

	model.msgChan = make(chan tea.Msg, 1)
	model.msgChan <- ErrorMsg{Error: errors.New("boom")}
	withChan := model.listenForMessages()
	received := withChan()
	channelMsg, ok := received.(ChannelMsg)
	if !ok {
		t.Fatalf("expected ChannelMsg, got %T", received)
	}
	if _, ok := channelMsg.Msg.(ErrorMsg); !ok {
		t.Fatalf("expected ErrorMsg")
	}
}

func TestLoadGlobalHistory(t *testing.T) {
	ensureTheme()
	rpc := &stubRPC{
		userHistoryResp: []UserHistoryItem{
			{Content: "hello"},
			{Content: "hello"},
			{Content: "world"},
		},
	}
	model := NewModel(rpc, "Agent", "", false, "", false, "")
	ch := make(chan tea.Msg, 1)
	model.msgChan = ch
	_, _ = model.loadGlobalHistory()
	select {
	case msg := <-ch:
		loaded, ok := msg.(GlobalHistoryLoadedMsg)
		if !ok {
			t.Fatalf("expected GlobalHistoryLoadedMsg, got %T", msg)
		}
		if len(loaded.Items) != 2 {
			t.Fatalf("expected deduplicated history, got %v", loaded.Items)
		}
	case <-time.After(2 * time.Second):
		t.Fatalf("timeout waiting for history")
	}

	rpc.userHistoryErr = errors.New("fail")
	chErr := make(chan tea.Msg, 1)
	model.msgChan = chErr
	_, _ = model.loadGlobalHistory()
	select {
	case msg := <-chErr:
		loaded, ok := msg.(GlobalHistoryLoadedMsg)
		if !ok || loaded.Error == nil {
			t.Fatalf("expected error load")
		}
	case <-time.After(2 * time.Second):
		t.Fatalf("timeout waiting for error history")
	}
}

func TestModelHandlesToolLifecycleAndStatus(t *testing.T) {
	ensureTheme()
	model := NewModel(nil, "Agent", "Welcome", false, "", false, "")
	model.height = 40
	model.state = StateWaitingForResponse
	model.currentRunID = "run-123"

	updated, _ := model.Update(ToolCalledMsg{ToolCalledEvent{Tool: "cmd", Input: "{}", CallID: "call"}})
	next, ok := updated.(Model)
	if !ok {
		t.Fatalf("expected Model, got %T", updated)
	}
	if len(next.messages) != 1 {
		t.Fatalf("expected 1 message, got %d", len(next.messages))
	}
	toolMsg := next.messages[0]
	if toolMsg.Role != "tool_call" {
		t.Fatalf("expected tool_call role, got %s", toolMsg.Role)
	}
	if toolMsg.ToolCall == nil || !toolMsg.ToolCall.Running {
		t.Fatalf("expected running tool call")
	}

	updated, _ = next.Update(ToolResultMsg{ToolResultEvent{Tool: "cmd", Output: "done", CallID: "call"}})
	next, ok = updated.(Model)
	if !ok {
		t.Fatalf("expected Model, got %T", updated)
	}
	tc := next.messages[0].ToolCall
	if tc == nil || tc.Result != "done" {
		t.Fatalf("expected tool result captured")
	}
	if tc.Running {
		t.Fatalf("expected tool call to stop running")
	}

	updated, _ = next.Update(RunStatusMsg{RunStatusEvent{RunID: "run-123", Message: "timeout", Attempt: 1, Total: 3}})
	next, ok = updated.(Model)
	if !ok {
		t.Fatalf("expected Model, got %T", updated)
	}
	if next.state != StateWaitingForResponse {
		t.Fatalf("expected waiting state")
	}
	if next.statusOverride == "" {
		t.Fatalf("expected status override")
	}

	updated, _ = next.Update(RunEndMsg{RunEndEvent{RunID: "run-123", EndReason: "success"}})
	final, ok := updated.(Model)
	if !ok {
		t.Fatalf("expected Model, got %T", updated)
	}
	if final.state != StateReady {
		t.Fatalf("expected ready state")
	}
	if final.statusOverride != "" {
		t.Fatalf("expected cleared status override, got %q", final.statusOverride)
	}
	if final.currentRunID != "" {
		t.Fatalf("expected cleared run id")
	}
}

func TestModelApprovalFlow(t *testing.T) {
	ensureTheme()
	model := NewModel(nil, "Agent", "Welcome", false, "", false, "")
	model.height = 40

	approvalMsg := ToolApprovalRequestMsg{ToolApprovalRequestEvent{RequestID: "req", ApprovalID: "appr", Tool: "shell", Arguments: "{}"}}
	updated, _ := model.Update(approvalMsg)
	next, ok := updated.(Model)
	if !ok {
		t.Fatalf("expected Model, got %T", updated)
	}
	if next.state != StateApprovalRequired {
		t.Fatalf("expected approval state")
	}
	if next.pendingTool == nil {
		t.Fatalf("expected pending tool")
	}
	if len(next.messages) != 1 {
		t.Fatalf("expected approval message")
	}

	next.approvalList.Select(2)
	resolved, cmd := next.handleApprovalListChoice()
	if cmd == nil {
		t.Fatalf("expected command for approval response")
	}
	if resolved.state != StateWaitingForResponse {
		t.Fatalf("expected waiting state after approval")
	}
	if resolved.pendingTool != nil {
		t.Fatalf("expected cleared pending tool")
	}
	if len(resolved.messages) != 0 {
		t.Fatalf("expected approval message removed")
	}
}

func TestModelUpdateCoversBranches(t *testing.T) {
	ensureTheme()
	rpc := &RPCClient{}
	model := NewModel(rpc, "Agent", "Welcome", false, "", false, "")
	model.width = 80
	model.height = 40
	model.msgChan = make(chan tea.Msg, 10)
	model.state = StateWaitingForResponse
	model.statusOverride = "Retrying"
	model.statusShowSpinner = true

	res, _ := model.Update(ChannelMsg{Msg: MessageOutputMsg{MessageOutputEvent{Content: "hi"}}})
	model = unwrapModel(t, res)
	if model.statusOverride != "" {
		t.Fatalf("expected status cleared")
	}
	if len(model.messages) == 0 {
		t.Fatalf("expected assistant message added")
	}

	approval := ToolApprovalRequestMsg{ToolApprovalRequestEvent{RequestID: "req", ApprovalID: "appr", Tool: "shell", Arguments: "{}"}}
	res, _ = model.Update(approval)
	model = unwrapModel(t, res)
	if model.state != StateApprovalRequired {
		t.Fatalf("expected approval state")
	}

	res, cmd := model.Update(tea.KeyMsg{Type: tea.KeyEsc})
	model = unwrapModel(t, res)
	if cmd == nil {
		t.Fatalf("expected command for denial")
	}
	if model.state != StateWaitingForResponse {
		t.Fatalf("expected waiting state after denial")
	}

	res, _ = model.Update(tea.KeyMsg{Type: tea.KeyCtrlC})
	model = unwrapModel(t, res)
	if model.firstCtrlCTime == nil {
		t.Fatalf("expected first ctrl+c recorded")
	}

	res, _ = model.Update(ClearCtrlCMsg{})
	model = unwrapModel(t, res)
	if model.firstCtrlCTime != nil {
		t.Fatalf("expected ctrl+c cleared")
	}

	model.state = StateWaitingForResponse
	model.currentRunID = "run"
	model.messages = []Message{{Role: "tool_call", ToolCall: &ToolCallInfo{Name: "tool", CallID: "c1", Running: true}}}
	res, cmd = model.Update(tea.KeyMsg{Type: tea.KeyEsc})
	model = unwrapModel(t, res)
	if cmd == nil {
		t.Fatalf("expected cancellation command")
	}
	if model.state != StateReady {
		t.Fatalf("expected ready state after cancel")
	}

	res, _ = model.Update(tea.WindowSizeMsg{Width: 90, Height: 32})
	model = unwrapModel(t, res)
	if model.width != 90 || model.height != 32 {
		t.Fatalf("expected window size applied")
	}

	res, _ = model.Update(ConnectedMsg{})
	model = unwrapModel(t, res)

	res, _ = model.Update(ErrorMsg{Error: errors.New("boom")})
	model = unwrapModel(t, res)
	if model.state != StateReady {
		t.Fatalf("expected ready state on error")
	}

	res, _ = model.Update(TokenUsageMsg{TokenUsageEvent{Totals: UsageDetails{TotalTokens: 10}, Model: "gpt", ContextWindow: 100, Delta: UsageDetails{InputTokens: 3}}})
	model = unwrapModel(t, res)
	if model.modelName != "gpt" {
		t.Fatalf("expected model metadata captured")
	}

	res, _ = model.Update(CommandResultMsg{Content: "done"})
	model = unwrapModel(t, res)
	if len(model.messages) == 0 {
		t.Fatalf("expected command result message")
	}

	res, _ = model.Update(ClientRequestMsg{ClientRequestEvent{RequestID: "r1", Function: "other", Args: map[string]interface{}{"text": "hi"}}})
	model = unwrapModel(t, res)

	res, _ = model.Update(RPCReadyMsg{})
	model = unwrapModel(t, res)

	sessions := []Session{{ID: "s1", MessageCount: 2}, {ID: "s2", MessageCount: 3}}
	res, _ = model.Update(SessionsListedMsg{Sessions: sessions})
	model = unwrapModel(t, res)
	if model.state != StateSessionList {
		t.Fatalf("expected session list state")
	}

	res, _ = model.Update(tea.KeyMsg{Type: tea.KeyDown})
	model = unwrapModel(t, res)
	res, _ = model.Update(tea.KeyMsg{Type: tea.KeyUp})
	model = unwrapModel(t, res)
	res, _ = model.Update(tea.KeyMsg{Type: tea.KeyRight})
	model = unwrapModel(t, res)
	res, _ = model.Update(tea.KeyMsg{Type: tea.KeyLeft})
	model = unwrapModel(t, res)

	res, _ = model.Update(tea.KeyMsg{Type: tea.KeyTab})
	model = unwrapModel(t, res)
	if model.state != StateReady {
		t.Fatalf("expected ready state after new session")
	}

	res, _ = model.Update(GlobalHistoryLoadedMsg{Items: []string{"hello"}})
	model = unwrapModel(t, res)
	if len(model.globalHistory) != 1 {
		t.Fatalf("expected history loaded")
	}

	res, _ = model.Update(FallbackToLLMMsg{Text: "/command"})
	model = unwrapModel(t, res)
	if len(model.messages) == 0 {
		t.Fatalf("expected user message added")
	}

	res, _ = model.Update(PulseTickMsg{})
	model = unwrapModel(t, res)

	res, _ = model.Update(spinner.TickMsg{})
	model = unwrapModel(t, res)

	res, _ = model.Update(tea.MouseMsg{Type: tea.MouseLeft})
	model = unwrapModel(t, res)

	res, _ = model.Update(ChannelMsg{Msg: nil})
	model = unwrapModel(t, res)

	model.globalHistory = []string{"first", "second"}
	model.historyIndex = len(model.globalHistory)
	model.state = StateReady
	res, _ = model.Update(tea.KeyMsg{Type: tea.KeyUp})
	model = unwrapModel(t, res)
	res, _ = model.Update(tea.KeyMsg{Type: tea.KeyDown})
	model = unwrapModel(t, res)

	_ = model.View()
	model.state = StateConnecting
	_ = model.View()
	model.state = StateSessionList
	model.availableSessions = []Session{{ID: "abc", MessageCount: 1}}
	model.paginator.SetTotalPages(len(model.availableSessions))
	_ = model.View()

	model.state = StateReady
	model.messages = []Message{{Role: "assistant", Content: "hello"}}
	_ = model.renderMessages()
	_ = model.renderStatusLine()

	model.state = StateReady
	model.statusOverride = "Error: test"
	model.statusShowSpinner = false
	status := model.renderStatusLine()
	if !strings.Contains(status, "Error: test") {
		t.Fatalf("expected status override content")
	}

	model.state = StateReady
	zero := time.Now()
	model.firstCtrlCTime = &zero
	help := model.getContextualHelp()
	if help == "" {
		t.Fatalf("expected contextual help text")
	}
}

func TestModelSendMessageTransitions(t *testing.T) {
	ensureTheme()
	model := NewModel(&RPCClient{}, "Agent", "Welcome", false, "", false, "")
	model.height = 40
	model.input.SetValue(" hello ")
	next, cmd := model.sendMessage()
	if cmd == nil {
		t.Fatalf("expected command returned")
	}
	if next.state != StateWaitingForResponse {
		t.Fatalf("expected waiting state")
	}
	if len(next.messages) != 1 || next.messages[0].Content != "hello" {
		t.Fatalf("expected trimmed message stored")
	}
	if len(next.globalHistory) == 0 || next.globalHistory[len(next.globalHistory)-1] != "hello" {
		t.Fatalf("expected history updated")
	}
}

func TestHandleSlashCommandExit(t *testing.T) {
	ensureTheme()
	model := NewModel(nil, "Agent", "", false, "", false, "")

	exitCmd := model.handleSlashCommand("/exit")
	if exitCmd == nil {
		t.Fatalf("expected quit command")
	}
	if msg := exitCmd(); msg != nil {
		if _, ok := msg.(tea.QuitMsg); !ok {
			t.Fatalf("expected QuitMsg, got %T", msg)
		}
	}
}
