package main

import (
	"fmt"
	"io"

	"github.com/charmbracelet/bubbles/list"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// Approval option styles - these are initialized from theme
var (
	approvalTitleStyle        lipgloss.Style
	approvalItemStyle         lipgloss.Style
	approvalSelectedItemStyle lipgloss.Style
	approvalDescStyle         lipgloss.Style
)

// initApprovalStyles initializes approval styles from theme
func initApprovalStyles() {
	approvalTitleStyle = Styles.ApprovalTitle
	approvalItemStyle = Styles.ApprovalOption.Copy().PaddingLeft(2)
	approvalSelectedItemStyle = Styles.ApprovalSelected.Copy().PaddingLeft(0)
	approvalDescStyle = Styles.Help.Copy().PaddingLeft(4)
}

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
// width should be the inner content width of the approval box (i.e., viewport width minus borders and padding)
func CreateApprovalList(toolName, toolArgs string, width int) list.Model {
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

	// Determine list width with sensible defaults and minimums
	listWidth := width
	if listWidth <= 0 {
		listWidth = 70 // fallback default
	}
	if listWidth < 30 {
		listWidth = 30 // prevent overly narrow rendering
	}
	
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