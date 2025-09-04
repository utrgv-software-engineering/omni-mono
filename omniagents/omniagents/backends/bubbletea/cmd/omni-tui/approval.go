package main

import (
	"fmt"
	"io"

	"github.com/charmbracelet/bubbles/list"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// Approval option styles
var (
	approvalTitleStyle        = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("226"))
	approvalItemStyle         = lipgloss.NewStyle().PaddingLeft(2)
	approvalSelectedItemStyle = lipgloss.NewStyle().PaddingLeft(0).Foreground(lipgloss.Color("205")).Bold(true)
	approvalDescStyle        = lipgloss.NewStyle().Foreground(lipgloss.Color("245")).PaddingLeft(4)
)

// ApprovalOption represents a choice in the approval dialog
type ApprovalOption struct {
	Title       string
	Description string
	Value       string // "yes", "always", "no"
}

func (i ApprovalOption) FilterValue() string { return i.Title }

// ApprovalDelegate is the item delegate for approval options
type ApprovalDelegate struct{}

func (d ApprovalDelegate) Height() int                             { return 1 } // Single line
func (d ApprovalDelegate) Spacing() int                            { return 0 }
func (d ApprovalDelegate) Update(_ tea.Msg, _ *list.Model) tea.Cmd { return nil }

func (d ApprovalDelegate) Render(w io.Writer, m list.Model, index int, listItem list.Item) {
	i, ok := listItem.(ApprovalOption)
	if !ok {
		return
	}

	// Combine title and description on one line
	str := fmt.Sprintf("%s - %s", i.Title, i.Description)
	
	if index == m.Index() {
		// Selected item with arrow
		fmt.Fprint(w, approvalSelectedItemStyle.Render("▶ " + str))
	} else {
		// Unselected item with padding
		fmt.Fprint(w, approvalItemStyle.Render(str))
	}
}

// CreateApprovalList creates a new list for tool approval
func CreateApprovalList(toolName, toolArgs string) list.Model {
	items := []list.Item{
		ApprovalOption{
			Title:       "1. Approve Once",
			Description: "Allow this tool call only",
			Value:       "yes",
		},
		ApprovalOption{
			Title:       "2. Always Approve", 
			Description: "Allow this and future calls in this session",
			Value:       "always",
		},
		ApprovalOption{
			Title:       "3. Deny",
			Description: "Cancel this tool call",
			Value:       "no",
		},
	}

	const listWidth = 70  // Increased width to accommodate arguments
	
	// Create list with enough height to show all items without pagination
	delegate := ApprovalDelegate{}
	itemHeight := delegate.Height()
	spacing := delegate.Spacing()
	totalItemsHeight := len(items)*itemHeight + (len(items)-1)*spacing
	
	// Add extra height for title and arguments display
	// Title (1 line) + arguments (2-3 lines) + spacing (1 line) + items
	listHeight := totalItemsHeight + 5
	
	l := list.New(items, delegate, listWidth, listHeight)
	
	// Set title to just the tool name
	// Arguments will be rendered separately in renderApprovalRequest
	title := fmt.Sprintf("Tool Approval: %s", toolName)
	l.Title = title
	l.Styles.Title = approvalTitleStyle
	
	// Disable features we don't need
	l.SetShowStatusBar(false)
	l.SetFilteringEnabled(false)
	l.SetShowPagination(false)  // Explicitly disable pagination
	l.SetShowHelp(false)  // Disable built-in help to use our custom help
	
	// Ensure we show all items
	l.DisableQuitKeybindings()
	
	return l
}