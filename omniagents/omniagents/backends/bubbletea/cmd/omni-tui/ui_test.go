package main

import (
	"strings"
	"testing"
)

func TestSafeTextSanitizesInput(t *testing.T) {
	ensureTheme()
	input := "\x1b[31mhello\x1b[0m" + string(rune(0x07))
	out := safeText(input)
	if strings.Contains(out, "\x1b") {
		t.Fatalf("expected ANSI sequences removed: %q", out)
	}
	if !strings.Contains(out, "hello") {
		t.Fatalf("expected content preserved")
	}
	if !strings.Contains(out, "\\x07") {
		t.Fatalf("expected control encoded")
	}
}

func TestRenderToolCallAndResult(t *testing.T) {
	ensureTheme()
	msg := Message{Role: "tool_call", ToolCall: &ToolCallInfo{Name: "search", Arguments: `{"query":"hello"}`, Running: true}}
	running := renderToolCallWithWidth(msg, false, 2, 40)
	if !strings.Contains(running, "search(") {
		t.Fatalf("expected tool name in output: %q", running)
	}

	msg.ToolCall.Running = false
	msg.ToolCall.Result = "line one\nline two"
	msg.ToolCall.Metadata = map[string]interface{}{"display_type": "error", "summary": "boom", "preview": "trace", "truncated": true}
	finished := renderToolCallWithWidth(msg, false, 0, 60)
	if !strings.Contains(finished, "boom") {
		t.Fatalf("expected summary in output: %q", finished)
	}
	if !strings.Contains(finished, "trace") {
		t.Fatalf("expected preview content: %q", finished)
	}
}

func TestCreateApprovalListSizing(t *testing.T) {
	ensureTheme()
	list := CreateApprovalList("tool", "{}", 10)
	if list.Width() < 30 {
		t.Fatalf("expected width >= 30, got %d", list.Width())
	}
	if len(list.Items()) != 3 {
		t.Fatalf("expected three approval options")
	}
}

func TestRenderStatusLineReflectsState(t *testing.T) {
	ensureTheme()
	model := NewModel(nil, "Agent", "", false, "", false, "")
	model.viewport.Width = 80
	model.state = StateWaitingForResponse
	model.currentRunID = "run"
	status := model.renderStatusLine()
	if !strings.Contains(status, "Thinking") {
		t.Fatalf("expected thinking status: %q", status)
	}

	model.statusOverride = "Error: failed"
	model.statusShowSpinner = false
	override := model.renderStatusLine()
	if !strings.Contains(override, "Error: failed") {
		t.Fatalf("expected override text: %q", override)
	}
}

func TestFormatToolArgumentsSmart(t *testing.T) {
	jsonArgs := `{"query":"hello world","limit":5}`
	formatted := formatToolArgumentsSmart(jsonArgs, 60)
	if !strings.Contains(formatted, "query:") || !strings.Contains(formatted, "limit:") {
		t.Fatalf("expected named fields: %q", formatted)
	}

	small := formatToolArgumentsSmart(jsonArgs, 5)
	if small != "..." {
		t.Fatalf("expected ellipsis for narrow width, got %q", small)
	}
}

func TestRenderMessagesHelpers(t *testing.T) {
	ensureTheme()
	user := renderUserMessage(Message{Role: "user", Content: "line1\n\nline2"})
	if !strings.Contains(user, "line1") || !strings.Contains(user, "line2") {
		t.Fatalf("expected user lines preserved: %q", user)
	}

	assistant := renderAssistantMessage(Message{Role: "assistant", Content: "# Title\n\nBody"})
	if !strings.Contains(assistant, "Title") {
		t.Fatalf("expected markdown rendered: %q", assistant)
	}

	approvalInfo := &ToolApprovalInfo{Tool: "shell", Arguments: "echo hi"}
	approvalList := CreateApprovalList("shell", "echo hi", 60)
	approvalMsg := Message{Role: "approval_request", Approval: approvalInfo}
	approval := renderMessageWithWidth(approvalMsg, false, 0, &approvalList, 60)
	if !strings.Contains(approval, "Tool Approval") {
		t.Fatalf("expected approval dialog output: %q", approval)
	}
}

func TestRichMetadataHelpers(t *testing.T) {
	ensureTheme()
	raw := map[string]interface{}{
		"display_type": "error",
		"summary":      "fail",
		"preview":      "line a\nline b",
		"truncated":    true,
		"metadata":     map[string]interface{}{"line_count": float64(4)},
	}
	meta := parseRichMetadata(raw)
	if meta == nil || meta.Summary != "fail" {
		t.Fatalf("expected parsed metadata")
	}
	preview := formatRichPreview(meta)
	if !strings.Contains(preview, "line a") {
		t.Fatalf("expected preview content: %q", preview)
	}
	if !strings.Contains(preview, "... +") {
		t.Fatalf("expected truncation indicator: %q", preview)
	}
}

func TestFormatSearchAndDirectoryPreviews(t *testing.T) {
	ensureTheme()
	search := &RichMetadata{DisplayType: "search_results", Preview: "file: match\nLine 42: text", Truncated: true}
	searchPreview := formatRichPreview(search)
	if !strings.Contains(searchPreview, "Line 42") {
		t.Fatalf("expected search line: %q", searchPreview)
	}

	directory := &RichMetadata{DisplayType: "directory_listing", Preview: "dir/\nfile.txt\nrun.sh", Truncated: true, Metadata: map[string]interface{}{}}
	dirPreview := formatRichPreview(directory)
	if !strings.Contains(dirPreview, "dir/") || !strings.Contains(dirPreview, "file.txt") {
		t.Fatalf("expected directory listing output: %q", dirPreview)
	}
}

func TestRenderToolResultVariants(t *testing.T) {
	ensureTheme()
	cancelled := renderToolResult(Message{Role: "tool_call", ToolCall: &ToolCallInfo{Result: "[Cancelled]"}})
	if !strings.Contains(cancelled, "Cancelled") {
		t.Fatalf("expected cancellation notice: %q", cancelled)
	}

	raw := Message{Role: "tool_call", ToolCall: &ToolCallInfo{Result: "\x1b[31mline\x1b[0m"}}
	plain := renderToolResult(raw)
	if strings.Contains(plain, "\x1b") {
		t.Fatalf("expected ANSI removed: %q", plain)
	}
	if !strings.Contains(plain, "line") {
		t.Fatalf("expected plain content: %q", plain)
	}
}

func TestWrapHelpers(t *testing.T) {
	lines := wrapText("one two three four", 5)
	if len(lines) < 2 {
		t.Fatalf("expected wrapped lines")
	}
	hard := wrapLineHard("abcdefgh", 3)
	if len(hard) != 3 || hard[0] != "abc" {
		t.Fatalf("expected hard wrap")
	}
}

func TestRenderApprovalRequestVariants(t *testing.T) {
	ensureTheme()
	baseList := CreateApprovalList("shell", "echo hi", 60)
	baseMsg := Message{Role: "approval_request", Approval: &ToolApprovalInfo{Tool: "shell", Arguments: "echo hi"}}
	base := renderApprovalRequest(baseMsg, baseList)
	if !strings.Contains(base, "Tool Approval") {
		t.Fatalf("expected approval text: %q", base)
	}

	diffMeta := map[string]interface{}{
		"display_type": "diff",
		"metadata":     map[string]interface{}{"file_path": "main.go"},
		"value":        map[string]interface{}{"diff_lines": []interface{}{"@@ -1 +1 @@", "-old", "+new"}},
	}
	diffMsg := Message{Role: "approval_request", Approval: &ToolApprovalInfo{Tool: "shell", Arguments: "echo hi", Metadata: diffMeta}}
	diff := renderApprovalRequestWithWidth(diffMsg, baseList, 80)
	if !strings.Contains(diff, "main.go") {
		t.Fatalf("expected diff metadata: %q", diff)
	}

	fileMeta := map[string]interface{}{
		"display_type": "file_write",
		"summary":      "Write File",
		"metadata":     map[string]interface{}{"file_path": "code.go"},
		"value":        "package main\nfunc main() {}\n",
	}
	fileMsg := Message{Role: "approval_request", Approval: &ToolApprovalInfo{Tool: "write", Arguments: "{}", Metadata: fileMeta}}
	fileOut := renderApprovalRequestWithWidth(fileMsg, baseList, 70)
	if !strings.Contains(fileOut, "Write File") {
		t.Fatalf("expected file summary: %q", fileOut)
	}

	cmdMeta := map[string]interface{}{
		"display_type": "command",
		"summary":      "Run Command",
		"metadata":     map[string]interface{}{"command": "ls -la"},
	}
	cmdMsg := Message{Role: "approval_request", Approval: &ToolApprovalInfo{Tool: "bash", Arguments: "{}", Metadata: cmdMeta}}
	cmdOut := renderApprovalRequestWithWidth(cmdMsg, baseList, 75)
	if !strings.Contains(cmdOut, "ls -la") {
		t.Fatalf("expected command rendered: %q", cmdOut)
	}
}

func TestRenderRichToolResultVariants(t *testing.T) {
	ensureTheme()
	diffMeta := &RichMetadata{
		DisplayType: "diff",
		Metadata:    map[string]interface{}{},
		Value:       map[string]interface{}{"diff_lines": []interface{}{"@@ -1 +1 @@", "-old", "+new"}},
		Truncated:   true,
	}
	diffOut := renderRichToolResultWithWidth(diffMeta, "", 80)
	if !strings.Contains(diffOut, "new") {
		t.Fatalf("expected diff output: %q", diffOut)
	}

	tableMeta := &RichMetadata{
		DisplayType: "table",
		Table: map[string]interface{}{
			"columns": []interface{}{
				map[string]interface{}{"title": "Name", "width": float64(10)},
				map[string]interface{}{"title": "Value", "width": float64(10)},
			},
			"rows": []interface{}{
				[]interface{}{"alpha", 1},
				[]interface{}{"beta", 2},
			},
			"height": float64(5),
		},
		Metadata:  map[string]interface{}{"total_rows": float64(5)},
		Truncated: true,
	}
	tableOut := renderTableRichWithWidth(tableMeta, 80)
	if !strings.Contains(tableOut, "alpha") {
		t.Fatalf("expected table content: %q", tableOut)
	}

	previewMeta := &RichMetadata{
		DisplayType: "error",
		Preview:     "line one\nline two",
		Truncated:   true,
		Metadata:    map[string]interface{}{"line_count": float64(4)},
	}
	previewOut := formatRichPreviewWithWidth(previewMeta, 60)
	if !strings.Contains(previewOut, "line one") {
		t.Fatalf("expected preview content: %q", previewOut)
	}

	diffPreview := renderDiffToolPreviewWithWidth(diffMeta, 80)
	if !strings.Contains(diffPreview, "new") {
		t.Fatalf("expected diff preview")
	}
}

func TestFormatToolArgumentsVariants(t *testing.T) {
	obj := `{"cmd":"run","values":[1,2,3],"long":"` + strings.Repeat("x", 80) + `"}`
	formatted := formatToolArguments(obj)
	if !strings.Contains(formatted, "cmd") {
		t.Fatalf("expected formatted args: %q", formatted)
	}
	short := formatToolArgumentsWithLimit(obj, 40)
	if len(short) == 0 {
		t.Fatalf("expected truncated args")
	}
}

func TestRenderMessageRouting(t *testing.T) {
	ensureTheme()
	msg := Message{Role: "approval_request", Approval: &ToolApprovalInfo{Tool: "shell", Arguments: "echo"}}
	list := CreateApprovalList("shell", "echo", 60)
	rendered := renderMessageWithWidth(msg, false, 0, &list, 60)
	if !strings.Contains(rendered, "Tool Approval") {
		t.Fatalf("expected approval render")
	}
	userMsg := Message{Role: "user", Content: "hello"}
	if !strings.Contains(renderMessageWithWidth(userMsg, false, 0, &list, 60), "hello") {
		t.Fatalf("expected user content")
	}
}
