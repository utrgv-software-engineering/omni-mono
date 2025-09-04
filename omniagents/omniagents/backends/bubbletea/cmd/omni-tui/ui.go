package main

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/charmbracelet/bubbles/list"
	"github.com/charmbracelet/glamour"
	"github.com/charmbracelet/lipgloss"
	"github.com/iancoleman/orderedmap"
)

// Style definitions
var (
	userStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("10")) // Green

	assistantStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("15")) // White

	toolStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("14")). // Cyan
			Bold(true)

	toolNameStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("14")). // Cyan
			Bold(true)

	toolResultStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("252")) // Much lighter gray, almost white for better visibility

	errorStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("196")). // Red
			Bold(true)

	debugStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("241")). // Gray
			Italic(true)
	
	// Glamour renderer for markdown (lazy initialized)
	glamourRenderer *glamour.TermRenderer
)

// RichOutput represents the rich tool output format
type RichOutput struct {
	OmniOutput   bool                   `json:"__omni_output__"`
	Value        interface{}            `json:"value"`
	DisplayType  string                 `json:"display_type"`
	Summary      string                 `json:"summary"`
	Preview      string                 `json:"preview,omitempty"`
	Truncated    bool                   `json:"truncated"`
	Metadata     map[string]interface{} `json:"metadata,omitempty"`
}

// parseRichOutput attempts to parse tool output as rich format
func parseRichOutput(output string) *RichOutput {
	var rich RichOutput
	if err := json.Unmarshal([]byte(output), &rich); err != nil {
		return nil
	}
	
	// Verify it's actually rich output format
	if !rich.OmniOutput {
		return nil
	}
	
	return &rich
}

// formatRichPreview formats preview content based on display type
func formatRichPreview(rich *RichOutput) string {
	var result strings.Builder
	previewLines := strings.Split(rich.Preview, "\n")
	maxPreviewLines := 10
	
	// Choose preview style based on display type
	var previewStyle lipgloss.Style
	switch rich.DisplayType {
	case "error":
		// Error output in red
		previewStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("196")) // Red
			
	case "search_results":
		// Search results need special handling
		return formatSearchResults(rich)
		
	case "directory_listing":
		// Directory listing with file type indicators
		return formatDirectoryListing(rich)
		
	default:
		// All other types use the default style
		previewStyle = toolResultStyle
	}
	
	// Show preview lines with proper indentation
	for i, line := range previewLines {
		if i >= maxPreviewLines {
			break
		}
		result.WriteString(previewStyle.Render("     " + line))
		result.WriteString("\n")
	}
	
	// Add truncation indicator if needed
	if rich.Truncated {
		result.WriteString(formatTruncationIndicator(rich, len(previewLines), maxPreviewLines))
	}
	
	return result.String()
}

// formatTruncationIndicator creates a truncation message based on metadata
func formatTruncationIndicator(rich *RichOutput, shownLines, maxShown int) string {
	truncationStyle := lipgloss.NewStyle().
		Foreground(lipgloss.Color("245")). // Darker gray
		Italic(true)
	
	// Build truncation message based on metadata
	truncMsg := "     ..."
	
	if rich.Metadata != nil {
		// Check for line count information
		if totalLines, ok := rich.Metadata["line_count"].(float64); ok {
			if shownLines > maxShown {
				shownLines = maxShown
			}
			remaining := int(totalLines) - shownLines
			if remaining > 0 {
				truncMsg = fmt.Sprintf("     ... +%d more lines", remaining)
			}
		} else if totalBytes, ok := rich.Metadata["total_bytes"].(float64); ok {
			// Alternative: show bytes if lines not available
			truncMsg = fmt.Sprintf("     ... (%d bytes total)", int(totalBytes))
		}
		
		// Special handling for command output
		if rich.DisplayType == "command" {
			if exitCode, ok := rich.Metadata["exit_code"].(float64); ok {
				exitStyle := truncationStyle
				if exitCode != 0 {
					// Show non-zero exit codes in yellow
					exitStyle = lipgloss.NewStyle().
						Foreground(lipgloss.Color("226")). // Yellow
						Italic(true)
				}
				truncMsg += exitStyle.Render(fmt.Sprintf(" (exit: %d)", int(exitCode)))
			}
		}
	}
	
	return truncationStyle.Render(truncMsg) + "\n"
}

// formatSearchResults formats search results with match highlighting
func formatSearchResults(rich *RichOutput) string {
	var result strings.Builder
	lines := strings.Split(rich.Preview, "\n")
	
	fileStyle := lipgloss.NewStyle().Bold(true)
	matchStyle := lipgloss.NewStyle() // No special formatting
	lineNumStyle := lipgloss.NewStyle().Foreground(lipgloss.Color("245")) // Keep gray for line numbers
	
	for i, line := range lines {
		if i >= 10 {
			break
		}
		
		// Check if this looks like a filename line (e.g., "path/to/file: 3 matches")
		if strings.Contains(line, ": ") && strings.Contains(line, "match") {
			result.WriteString("     ")
			result.WriteString(fileStyle.Render(line))
			result.WriteString("\n")
		} else if strings.Contains(line, "Line ") {
			// Match line (e.g., "Line 42: # TODO: implement")
			parts := strings.SplitN(line, ":", 2)
			if len(parts) == 2 {
				result.WriteString("       ")
				result.WriteString(lineNumStyle.Render(parts[0] + ":"))
				result.WriteString(matchStyle.Render(parts[1]))
				result.WriteString("\n")
			} else {
				result.WriteString("       " + line + "\n")
			}
		} else {
			result.WriteString("     " + line + "\n")
		}
	}
	
	if rich.Truncated {
		result.WriteString(formatTruncationIndicator(rich, len(lines), 10))
	}
	
	return result.String()
}

// formatDirectoryListing formats directory listings with file type indicators
func formatDirectoryListing(rich *RichOutput) string {
	var result strings.Builder
	lines := strings.Split(rich.Preview, "\n")
	
	dirStyle := lipgloss.NewStyle().Bold(true) // Bold for directories
	fileStyle := lipgloss.NewStyle()            // Normal for files  
	execStyle := lipgloss.NewStyle()            // Normal for executables
	
	for i, line := range lines {
		if i >= 10 {
			break
		}
		
		// Simple heuristic: directories often end with /
		// Executables might have specific extensions or indicators
		if strings.HasSuffix(line, "/") {
			result.WriteString("     ")
			result.WriteString(dirStyle.Render(line))
			result.WriteString("\n")
		} else if strings.Contains(line, ".exe") || strings.Contains(line, ".sh") || 
			      strings.Contains(line, ".bat") || strings.HasPrefix(line, "*") {
			result.WriteString("     ")
			result.WriteString(execStyle.Render(line))
			result.WriteString("\n")
		} else {
			result.WriteString("     ")
			result.WriteString(fileStyle.Render(line))
			result.WriteString("\n")
		}
	}
	
	if rich.Truncated {
		// Show file count if available
		if rich.Metadata != nil {
			if fileCount, ok := rich.Metadata["file_count"].(float64); ok {
				if dirCount, ok := rich.Metadata["dir_count"].(float64); ok {
					truncStyle := lipgloss.NewStyle().
						Foreground(lipgloss.Color("245")).
						Italic(true)
					msg := fmt.Sprintf("     ... %d files, %d directories total", 
						int(fileCount), int(dirCount))
					result.WriteString(truncStyle.Render(msg))
					result.WriteString("\n")
					return result.String()
				}
			}
		}
		result.WriteString(formatTruncationIndicator(rich, len(lines), 10))
	}
	
	return result.String()
}

// getDisplayTypeStyle returns icon and style for each display type
func getDisplayTypeStyle(displayType string) (string, lipgloss.Style) {
	switch displayType {
	case "error":
		// Errors in red
		return "", lipgloss.NewStyle().
			Foreground(lipgloss.Color("196")). // Red
			Bold(true)
	default:
		// All other display types use bold only
		return "", lipgloss.NewStyle().Bold(true)
	}
}

// getGlamourRenderer returns a cached glamour renderer
func getGlamourRenderer() *glamour.TermRenderer {
	if glamourRenderer == nil {
		// Custom style based on dark theme but with no margins
		customStyle := `{
			"document": {
				"block_prefix": "",
				"block_suffix": "\n",
				"color": "252",
				"margin": 0
			},
			"paragraph": {
				"margin": 0
			},
			"heading": {
				"block_suffix": "\n",
				"color": "39",
				"bold": true
			},
			"h1": {
				"prefix": " ",
				"suffix": " ",
				"color": "228",
				"background_color": "63",
				"bold": true
			},
			"h2": {
				"prefix": "## "
			},
			"h3": {
				"prefix": "### "
			},
			"code_block": {
				"color": "244",
				"margin": 0,
				"chroma": {
					"text": {
						"color": "#C4C4C4"
					},
					"error": {
						"color": "#F1F1F1",
						"background_color": "#F05B5B"
					},
					"comment": {
						"color": "#676767"
					},
					"comment_preproc": {
						"color": "#FF875F"
					},
					"keyword": {
						"color": "#00AAFF"
					},
					"keyword_reserved": {
						"color": "#FF5FD2"
					},
					"keyword_namespace": {
						"color": "#FF5F87"
					},
					"keyword_type": {
						"color": "#6E6ED8"
					},
					"operator": {
						"color": "#EF8080"
					},
					"punctuation": {
						"color": "#E8E8A8"
					},
					"name": {
						"color": "#C4C4C4"
					},
					"name_builtin": {
						"color": "#FF8EC7"
					},
					"name_tag": {
						"color": "#B083EA"
					},
					"name_attribute": {
						"color": "#7A7AE6"
					},
					"name_class": {
						"color": "#F1F1F1",
						"underline": true,
						"bold": true
					},
					"name_decorator": {
						"color": "#FFFF87"
					},
					"name_function": {
						"color": "#00D787"
					},
					"literal_number": {
						"color": "#6EEFC0"
					},
					"literal_string": {
						"color": "#C69669"
					},
					"literal_string_escape": {
						"color": "#AFFFD7"
					},
					"generic_deleted": {
						"color": "#FD5B5B"
					},
					"generic_emph": {
						"italic": true
					},
					"generic_inserted": {
						"color": "#00D787"
					},
					"generic_strong": {
						"bold": true
					},
					"generic_subheading": {
						"color": "#777777"
					},
					"background": {
						"background_color": "#373737"
					}
				}
			},
			"list": {
				"level_indent": 2
			},
			"item": {
				"block_prefix": "• "
			},
			"enumeration": {
				"block_prefix": ". "
			},
			"task": {
				"ticked": "[✓] ",
				"unticked": "[ ] "
			},
			"link": {
				"color": "30",
				"underline": true
			},
			"link_text": {
				"color": "35",
				"bold": true
			},
			"strikethrough": {
				"crossed_out": true
			},
			"emph": {
				"italic": true
			},
			"strong": {
				"bold": true
			},
			"code": {
				"prefix": " ",
				"suffix": " ",
				"color": "203",
				"background_color": "236"
			}
		}`
		
		r, _ := glamour.NewTermRenderer(
			glamour.WithStylesFromJSONBytes([]byte(customStyle)),
			glamour.WithWordWrap(76), // Leave room for indicator column
		)
		glamourRenderer = r
	}
	return glamourRenderer
}

// renderMessage renders a single message with appropriate formatting
func renderMessage(msg Message, debugMode bool, pulseFrame int, approvalList *list.Model) string {
	switch msg.Role {
	case "user":
		return renderUserMessage(msg)
	case "assistant":
		return renderAssistantMessage(msg)
	case "tool_call":
		return renderToolCall(msg, debugMode, pulseFrame)
	case "tool_result":
		// Always show tool results, not just in debug mode
		return renderToolResult(msg)
	case "approval_request":
		if approvalList != nil {
			return renderApprovalRequest(msg, *approvalList)
		}
		return ""
	default:
		return ""
	}
}

// renderUserMessage renders a user message
func renderUserMessage(msg Message) string {
	prefix := userStyle.Render("> ")
	content := msg.Content

	// Wrap long lines
	lines := wrapText(content, 80)
	result := prefix + lines[0] + "\n"
	for i := 1; i < len(lines); i++ {
		result += "  " + lines[i] + "\n"
	}
	
	// Add spacing after user message
	return result + "\n"
}

// renderAssistantMessage renders an assistant message with markdown support
func renderAssistantMessage(msg Message) string {
	if msg.Content == "" {
		return ""
	}

	// Try to render with Glamour for markdown formatting
	renderer := getGlamourRenderer()
	if renderer != nil {
		// Render the markdown content
		rendered, err := renderer.Render(msg.Content)
		if err == nil && rendered != "" {
			// Now that glamour has no margin, we can simply add our prefix
			prefix := assistantStyle.Render("⏺ ")
			lines := strings.Split(strings.TrimRight(rendered, "\n"), "\n")
			
			var result strings.Builder
			for i, line := range lines {
				if i == 0 {
					// First line gets the indicator
					result.WriteString(prefix)
				} else {
					// Other lines get spacing to align
					result.WriteString("  ")
				}
				result.WriteString(line)
				result.WriteString("\n")
			}
			result.WriteString("\n")
			return result.String()
		}
	}

	// Fallback to simple rendering if Glamour fails
	prefix := assistantStyle.Render("⏺ ")
	lines := wrapText(msg.Content, 78)
	if len(lines) == 0 {
		return ""
	}
	
	result := prefix + lines[0] + "\n"
	for i := 1; i < len(lines); i++ {
		result += "  " + lines[i] + "\n"
	}
	
	// Add spacing after assistant message
	return result + "\n"
}

// renderToolCall renders a tool call with formatted arguments
func renderToolCall(msg Message, debugMode bool, pulseFrame int) string {
	if msg.ToolCall == nil {
		return ""
	}

	// Parse and format arguments
	formattedArgs := formatToolArguments(msg.ToolCall.Arguments)

	// Create pulsing dot for running tools
	var indicator string
	if msg.ToolCall.Result == "" {
		// Tool is still running - create pulsing dot
		// Use different brightness levels based on pulse frame
		brightness := []string{"237", "240", "243", "246", "249", "246", "243", "240"}
		color := brightness[pulseFrame%8]
		pulsingDot := lipgloss.NewStyle().Foreground(lipgloss.Color(color)).Render("⏺")
		indicator = pulsingDot
	} else {
		// Tool has completed - use static dot
		indicator = toolStyle.Render("⏺")
	}
	
	toolName := toolNameStyle.Render(msg.ToolCall.Name)
	
	// Start with spacing and add spacing at the end
	result := fmt.Sprintf("%s %s(%s)\n", indicator, toolName, formattedArgs)

	// Always add result if available (not just in debug mode)
	if msg.ToolCall.Result != "" {
		result += renderToolResult(msg)
	}

	// Add spacing after tool call
	return result + "\n"
}

// renderToolResult renders tool output
func renderToolResult(msg Message) string {
	if msg.ToolCall == nil || msg.ToolCall.Result == "" {
		return ""
	}

	// Try to parse as rich output format
	richOutput := parseRichOutput(msg.ToolCall.Result)
	
	// Phase 2: Basic formatting for rich output (summary + preview)
	if richOutput != nil {
		return renderRichToolResult(richOutput)
	}
	
	// Fallback to plain text rendering for non-rich output
	result := msg.ToolCall.Result
	
	// Truncate long results
	if len(result) > 500 {
		result = result[:500] + "..."
	}

	// Format as indented output with a more visible style
	lines := strings.Split(result, "\n")
	// Apply the same style to both the prefix and the content for consistency
	formatted := toolResultStyle.Render("  └─ " + lines[0]) + "\n"
	for i := 1; i < len(lines) && i < 10; i++ {
		formatted += toolResultStyle.Render("     " + lines[i]) + "\n"
	}

	return formatted
}

// renderRichToolResult renders tool output in rich format (Phase 3)
func renderRichToolResult(rich *RichOutput) string {
	var result strings.Builder
	
	// Get type-specific formatting
	icon, summaryStyle := getDisplayTypeStyle(rich.DisplayType)
	
	// Start with the summary line with type indicator
	result.WriteString("  └─ ")
	if icon != "" {
		result.WriteString(icon + " ")
	}
	result.WriteString(summaryStyle.Render(rich.Summary))
	result.WriteString("\n")
	
	// Add preview if available with type-specific formatting
	if rich.Preview != "" {
		// Apply type-specific preview formatting
		result.WriteString(formatRichPreview(rich))
	} else if rich.Value != nil {
		// If no preview but we have a value, show a snippet of the value
		var valueStr string
		switch v := rich.Value.(type) {
		case string:
			valueStr = v
		default:
			if jsonBytes, err := json.Marshal(v); err == nil {
				valueStr = string(jsonBytes)
			}
		}
		
		if valueStr != "" {
			lines := strings.Split(valueStr, "\n")
			for i, line := range lines {
				if i >= 5 { // Limit to 5 lines if no explicit preview
					if len(lines) > 5 {
						truncStyle := lipgloss.NewStyle().
							Foreground(lipgloss.Color("245")).
							Italic(true)
						result.WriteString(truncStyle.Render(fmt.Sprintf("     ... +%d more lines", len(lines)-5)))
						result.WriteString("\n")
					}
					break
				}
				result.WriteString(toolResultStyle.Render("     " + line))
				result.WriteString("\n")
			}
		}
	}
	
	return result.String()
}

// formatToolArguments formats tool arguments for display
func formatToolArguments(argsStr string) string {
	return formatToolArgumentsWithLimit(argsStr, 100)
}

// formatToolArgumentsWithLimit formats tool arguments with a custom length limit
// Pass 0 or negative value for maxLen to disable truncation
func formatToolArgumentsWithLimit(argsStr string, maxLen int) string {
	// Try to parse as JSON using orderedmap to preserve field order
	om := orderedmap.New()
	if err := json.Unmarshal([]byte(argsStr), om); err == nil {
		// Successfully parsed as JSON object with preserved order
		parts := []string{}
		for _, k := range om.Keys() {
			v, _ := om.Get(k)
			
			// Format value based on type
			var valueStr string
			switch val := v.(type) {
			case string:
				// Quote strings
				valueStr = fmt.Sprintf("%q", val)
				// Truncate long strings
				if len(valueStr) > 50 {
					valueStr = valueStr[:47] + "...\""
				}
			case float64:
				// Format numbers
				if val == float64(int(val)) {
					valueStr = fmt.Sprintf("%d", int(val))
				} else {
					valueStr = fmt.Sprintf("%g", val)
				}
			case bool:
				valueStr = fmt.Sprintf("%v", val)
			case nil:
				valueStr = "null"
			default:
				// Complex types: arrays, objects
				if jsonBytes, err := json.Marshal(val); err == nil {
					valueStr = string(jsonBytes)
					if len(valueStr) > 50 {
						valueStr = valueStr[:47] + "..."
					}
				} else {
					valueStr = fmt.Sprintf("%v", val)
				}
			}
			parts = append(parts, fmt.Sprintf("%s: %s", k, valueStr))
		}
		
		// Join with commas
		formatted := strings.Join(parts, ", ")
		// Only truncate if maxLen is positive
		if maxLen > 0 && len(formatted) > maxLen {
			formatted = formatted[:maxLen-3] + "..."
		}
		return formatted
	}

	// Not JSON or failed to parse, return as-is (truncated if needed)
	if maxLen > 0 && len(argsStr) > maxLen {
		return argsStr[:maxLen-3] + "..."
	}
	return argsStr
}

// wrapText wraps text to the specified width
func wrapText(text string, width int) []string {
	if text == "" {
		return []string{""}
	}

	words := strings.Fields(text)
	if len(words) == 0 {
		return []string{""}
	}

	lines := []string{}
	currentLine := ""

	for _, word := range words {
		if currentLine == "" {
			currentLine = word
		} else if len(currentLine)+1+len(word) <= width {
			currentLine += " " + word
		} else {
			lines = append(lines, currentLine)
			currentLine = word
		}
	}

	if currentLine != "" {
		lines = append(lines, currentLine)
	}

	return lines
}

// renderWelcome renders the welcome message
func renderWelcome(welcomeText string) string {
	if welcomeText == "" {
		return ""
	}

	welcomeStyle := lipgloss.NewStyle().
		Foreground(lipgloss.Color("245")).
		Italic(true)

	lines := wrapText(welcomeText, 78)
	result := ""
	for _, line := range lines {
		result += welcomeStyle.Render(line) + "\n"
	}
	
	return result + "\n"
}

// renderApprovalRequest renders an approval request dialog in the message list
func renderApprovalRequest(msg Message, approvalList list.Model) string {
	if msg.Approval == nil {
		return ""
	}

	// Create a custom box with all the information
	// Since the list component doesn't handle multi-line titles well
	boxStyle := lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(lipgloss.Color("226")).
		Padding(1, 2).
		Width(70)

	titleStyle := lipgloss.NewStyle().
		Bold(true).
		Foreground(lipgloss.Color("226"))

	argsStyle := lipgloss.NewStyle().
		Foreground(lipgloss.Color("245"))

	var content strings.Builder
	
	// Title
	content.WriteString(titleStyle.Render(fmt.Sprintf("Tool Approval: %s", msg.Approval.Tool)))
	content.WriteString("\n\n")
	
	// Arguments - handle word wrapping properly
	// We need to wrap at ~65 chars (70 width - padding - border)
	maxArgWidth := 65
	argsLabel := "Arguments: "
	
	// First line with label
	if len(argsLabel) + len(msg.Approval.Arguments) <= maxArgWidth {
		// Fits on one line
		content.WriteString(argsStyle.Render(argsLabel + msg.Approval.Arguments))
	} else {
		// Need to wrap
		remaining := msg.Approval.Arguments
		firstLineLen := maxArgWidth - len(argsLabel)
		
		if len(remaining) > firstLineLen {
			// First line with label
			content.WriteString(argsStyle.Render(argsLabel + remaining[:firstLineLen]))
			content.WriteString("\n")
			remaining = remaining[firstLineLen:]
			
			// Continue on next lines with indent
			indent := "           " // Same width as "Arguments: "
			for len(remaining) > 0 {
				lineLen := len(remaining)
				if lineLen > maxArgWidth {
					lineLen = maxArgWidth
				}
				content.WriteString(argsStyle.Render(indent + remaining[:lineLen]))
				remaining = remaining[lineLen:]
				if len(remaining) > 0 {
					content.WriteString("\n")
				}
			}
		} else {
			content.WriteString(argsStyle.Render(argsLabel + remaining))
		}
	}
	content.WriteString("\n\n")
	
	// Add the list items from approvalList
	// We'll just render the raw list without its chrome
	listView := approvalList.View()
	// Try to extract just the list items from the view
	// The list view includes the title and items, we want to skip the title
	lines := strings.Split(listView, "\n")
	// Skip the title lines and just get the items
	itemsStarted := false
	for _, line := range lines {
		// Skip empty lines before items
		if !itemsStarted && strings.TrimSpace(line) == "" {
			continue
		}
		// Skip the title line
		if !itemsStarted && strings.Contains(line, "Tool Approval:") {
			continue
		}
		// Skip the "..." line
		if !itemsStarted && strings.TrimSpace(line) == "..." {
			continue
		}
		// Now we're at the items
		if strings.Contains(line, "Approve") || strings.Contains(line, "Deny") {
			itemsStarted = true
			content.WriteString(line)
			content.WriteString("\n")
		} else if itemsStarted && strings.TrimSpace(line) != "" {
			content.WriteString(line)
			content.WriteString("\n")
		}
	}

	return boxStyle.Render(content.String()) + "\n\n"
}

// renderStatusBar renders a status bar at the bottom
func renderStatusBar(state AppState, sessionID string, width int) string {
	statusStyle := lipgloss.NewStyle().
		Background(lipgloss.Color("235")).
		Foreground(lipgloss.Color("250")).
		Width(width).
		Padding(0, 1)

	var status string
	switch state {
	case StateConnecting:
		status = "Connecting..."
	case StateReady:
		status = "Ready"
	case StateWaitingForResponse:
		status = "Processing..."
	case StateApprovalRequired:
		status = "Approval Required"
	case StateError:
		status = "Error"
	case StateSessionList:
		status = "Select Session"
	default:
		status = "Unknown"
	}

	if sessionID != "" {
		status += fmt.Sprintf(" | Session: %s", sessionID[:8])
	}

	status += " | Ctrl+C to exit"

	return statusStyle.Render(status)
}