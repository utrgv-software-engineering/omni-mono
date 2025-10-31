package main

import (
	"errors"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func TestCustomTextareaOperations(t *testing.T) {
	ta := NewCustomTextarea()
	ta.Focus()
	if !ta.Focused() {
		t.Fatalf("expected focused state")
	}
	ta.SetWidth(30)
	ta.SetHeight(6)
	ta.SetPromptFunc(3, func(int) string { return "> " })

	sanitized := sanitizeUserInputRunes([]rune{13, 9, 'a', rune(0x1b), 10})
	if strings.ContainsRune(string(sanitized), rune(13)) {
		t.Fatalf("expected carriage return removed")
	}

	ta.SetValue("alpha beta\ngamma delta")
	ta.SetCursor(len(ta.value[0]))
	ta.insertRunesFromUserInput([]rune(" extra"))
	ta.deleteAfterCursor()
	ta.CursorStart()
	ta.CursorEnd()
	ta.CursorUp()
	ta.CursorDown()

	ta.deleteBeforeCursor()
	ta.insertRunesFromUserInput([]rune("replaced"))
	ta.SetCursor(len(ta.value[ta.row]))
	ta.transposeLeft()

	ta.deleteWordLeft()
	ta.insertRunesFromUserInput([]rune(" words"))
	ta.SetCursor(0)
	ta.deleteWordRight()
	ta.insertRunesFromUserInput([]rune("start "))

	ta.SetCursor(2)
	ta.characterLeft(false)
	ta.characterRight()
	ta.wordRight()
	ta.wordLeft()
	ta.uppercaseRight()
	ta.lowercaseRight()
	ta.capitalizeRight()
	ta.moveToBegin()
	ta.moveToEnd()

	ta.row = 0
	ta.mergeLineBelow(0)
	ta.insertRunesFromUserInput([]rune("\nthird line"))
	ta.row = 1
	ta.mergeLineAbove(1)
	ta.row = 0
	ta.splitLine(0, 2)

	info := ta.LineInfo()
	if info.Width == 0 {
		t.Fatalf("expected line width computed")
	}

	ta.repositionView()
	ta.Width()
	ta.Length()
	ta.LineCount()
	ta.Line()
	ta.View()
	ta.Placeholder = "hint"
	ta.Reset()
	ta.placeholderView()

	ta.SetCursor(1)
	ta, _ = ta.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'z'}})
	ta, _ = ta.Update(pasteMsg("clip"))
	ta, _ = ta.Update(pasteErrMsg{errors.New("paste")})
	if Paste() == nil {
		t.Fatalf("expected paste msg")
	}
	Blink()
	if ta.Err == nil {
		t.Fatalf("expected paste error stored")
	}

	ta.InsertRune('q')
	ta.Blur()
	if ta.Focused() {
		t.Fatalf("expected blurred state")
	}

	wrap([]rune("wrap words here"), 5)
	repeatSpaces(3)
	clamp(5, 1, 4)
	min(2, 3)
	max(2, 3)
}
