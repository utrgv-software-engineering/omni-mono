package main

import (
	"strings"
	"testing"
)

func TestFormatWithCommas(t *testing.T) {
	if formatWithCommas(1234567) != "1,234,567" {
		t.Fatalf("unexpected comma formatting")
	}
	if formatWithCommas(-42) != "-42" {
		t.Fatalf("negative formatting incorrect")
	}
}

func TestModelUsageFooter(t *testing.T) {
	ensureTheme()
	model := NewModel(nil, "Agent", "", false, "", false, "")
	model.modelName = "gpt"
	model.contextWindow = 100
	totals := UsageDetails{TotalTokens: 40}
	model.usageTotals = &totals
	text, pct, ok := model.getUsageFooterText()
	if !ok || pct != 60 {
		t.Fatalf("expected remaining percentage, got %d", pct)
	}
	if text == "" || !containsAll(text, []string{"context remaining", "gpt"}) {
		t.Fatalf("expected usage text: %q", text)
	}
}

func containsAll(haystack string, needles []string) bool {
	for _, n := range needles {
		if !strings.Contains(haystack, n) {
			return false
		}
	}
	return true
}

func TestRenderHeaderIncludesSession(t *testing.T) {
	ensureTheme()
	model := NewModel(nil, "Agent", "", false, "session123", false, "")
	header := model.renderHeader()
	if !strings.Contains(header, "Session") {
		t.Fatalf("expected session info: %q", header)
	}
}

func TestRenderInputShowsHelp(t *testing.T) {
	ensureTheme()
	model := NewModel(nil, "Agent", "", false, "", false, "")
	model.width = 80
	model.state = StateReady
	view := model.renderInput()
	if !strings.Contains(view, "enter send") {
		t.Fatalf("expected help text: %q", view)
	}
}

func TestApprovalOptionFilter(t *testing.T) {
	opt := ApprovalOption{Title: "Allow"}
	if opt.FilterValue() != "Allow" {
		t.Fatalf("unexpected filter value")
	}
}

func TestKeyMapHelp(t *testing.T) {
	short := keys.ShortHelp()
	if short == nil {
		t.Fatalf("expected short help slice")
	}
	full := keys.FullHelp()
	if len(full) != 3 {
		t.Fatalf("unexpected full help length: %d", len(full))
	}
}
