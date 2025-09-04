package main

import (
	"encoding/json"
	"fmt"
	"strings"

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
func renderMessage(msg Message, debugMode bool, pulseFrame int) string {
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

	// Truncate long results
	result := msg.ToolCall.Result
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

// formatToolArguments formats tool arguments for display
func formatToolArguments(argsStr string) string {
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
		if len(formatted) > 100 {
			formatted = formatted[:97] + "..."
		}
		return formatted
	}

	// Not JSON or failed to parse, return as-is (truncated)
	if len(argsStr) > 100 {
		return argsStr[:97] + "..."
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