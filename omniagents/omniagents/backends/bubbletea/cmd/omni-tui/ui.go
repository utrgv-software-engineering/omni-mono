package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/alecthomas/chroma/quick"
	"github.com/charmbracelet/bubbles/table"
	"github.com/charmbracelet/bubbles/list"
	"github.com/charmbracelet/glamour"
	"github.com/charmbracelet/lipgloss"
	"github.com/iancoleman/orderedmap"
	"github.com/lucasb-eyer/go-colorful"
)

// Glamour renderer for markdown (lazy initialized)
var glamourRenderer *glamour.TermRenderer

// Precompiled regex for ANSI escape sequences (CSI and OSC)
var (
	// CSI: ESC [ ... command
	reANSI = regexp.MustCompile(`\x1B\[[0-?]*[ -/]*[@-~]`)
	// OSC: ESC ] ... BEL or ST (ESC \\)
	reOSC = regexp.MustCompile(`\x1B\][^\x1B\x07]*(\x07|\x1B\\)`)
)

// stripANSI removes common ANSI escape sequences from a string
func stripANSI(s string) string {
	if s == "" {
		return s
	}
	s = reOSC.ReplaceAllString(s, "")
	s = reANSI.ReplaceAllString(s, "")
	return s
}

// sanitizeForTerminal replaces control characters (except newline and tab)
// with visible hexadecimal escapes like \x00 to prevent terminal glitches
func sanitizeForTerminal(s string) string {
	if s == "" {
		return s
	}
	var b strings.Builder
	// Heuristic grow to reduce allocations
	b.Grow(len(s) + 16)
	for _, r := range s {
		switch r {
		case '\n', '\t':
			b.WriteRune(r)
		default:
			if r < 0x20 || r == 0x7f { // control chars
				fmt.Fprintf(&b, "\\x%02X", r)
			} else {
				b.WriteRune(r)
			}
		}
	}
	return b.String()
}

// safeText applies ANSI stripping then control-char sanitization
func safeText(s string) string {
	if s == "" {
		return s
	}
	return sanitizeForTerminal(stripANSI(s))
}

// Legacy style definitions - these will be replaced by theme styles
// but kept for backward compatibility during transition
var (
	userStyle       lipgloss.Style
	assistantStyle  lipgloss.Style
	toolStyle       lipgloss.Style
	toolNameStyle   lipgloss.Style
	toolResultStyle lipgloss.Style
	errorStyle      lipgloss.Style
	debugStyle      lipgloss.Style
)

// initStyles initializes the style variables from the theme
func initStyles() {
	userStyle = Styles.UserMessage
	assistantStyle = Styles.AssistantMessage
	toolStyle = Styles.ToolName
	toolNameStyle = Styles.ToolName
	toolResultStyle = Styles.ToolResult
	errorStyle = Styles.Error
	debugStyle = Styles.Debug
}

// RichMetadata represents the rich tool output metadata
type RichMetadata struct {
	DisplayType string                 `json:"display_type"`
	Summary     string                 `json:"summary"`
	Preview     string                 `json:"preview,omitempty"`
	Truncated   bool                   `json:"truncated"`
	Metadata    map[string]interface{} `json:"metadata,omitempty"` // Contains all tool-specific metadata
	Chart       map[string]interface{} `json:"chart,omitempty"`
	Table       map[string]interface{} `json:"table,omitempty"`
	Value       map[string]interface{} `json:"value,omitempty"` // Raw value payload (e.g., diff_lines)
}

// parseRichMetadata converts metadata map to typed struct
func parseRichMetadata(metadata map[string]interface{}) *RichMetadata {
	if metadata == nil {
		return nil
	}

	rich := &RichMetadata{}

	if v, ok := metadata["display_type"].(string); ok {
		rich.DisplayType = v
	}
	if v, ok := metadata["summary"].(string); ok {
		rich.Summary = v
	}
	if v, ok := metadata["preview"].(string); ok {
		rich.Preview = v
	}
	if v, ok := metadata["truncated"].(bool); ok {
		rich.Truncated = v
	}
	if v, ok := metadata["metadata"].(map[string]interface{}); ok {
		rich.Metadata = v
	}
	if v, ok := metadata["chart"].(map[string]interface{}); ok {
		rich.Chart = v
	}
	if v, ok := metadata["table"].(map[string]interface{}); ok {
		rich.Table = v
	}

	// Include raw value payload used by some renderers (e.g., diff)
	if v, ok := metadata["value"].(map[string]interface{}); ok {
		rich.Value = v
	}

	return rich
}

// formatRichPreview formats preview content based on display type
func formatRichPreview(rich *RichMetadata) string {
	var result strings.Builder
	// Remove ANSI and sanitize preview before splitting
	prev := safeText(rich.Preview)
	previewLines := strings.Split(prev, "\n")
	maxPreviewLines := 10

	// Choose preview style based on display type
	var previewStyle lipgloss.Style
	switch rich.DisplayType {
	case "error":
		// Error output in red
		previewStyle = lipgloss.NewStyle().
			Foreground(Styles.Error.GetForeground())

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

	// Show preview lines with proper indentation and wrapping
	// Calculate available width (assume 80 char terminal - 5 spaces indent)
	maxLineWidth := 75
	linesShown := 0

	for _, line := range previewLines {
		if linesShown >= maxPreviewLines {
			break
		}

		// Wrap long lines at character boundaries
		wrappedParts := wrapLineHard(line, maxLineWidth)
		for _, part := range wrappedParts {
			if linesShown >= maxPreviewLines {
				break
			}
			result.WriteString(previewStyle.Render("     " + part))
			result.WriteString("\n")
			linesShown++
		}
	}

	// Add truncation indicator if needed
	if rich.Truncated {
		result.WriteString(formatTruncationIndicator(rich, len(previewLines), maxPreviewLines))
	}

	return result.String()
}

// formatTruncationIndicator creates a truncation message based on metadata
func formatTruncationIndicator(rich *RichMetadata, shownLines, maxShown int) string {
	truncationStyle := lipgloss.NewStyle().
		Foreground(Styles.Help.GetForeground()).
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
						Foreground(Styles.Warning.GetForeground()).
						Italic(true)
				}
				truncMsg += exitStyle.Render(fmt.Sprintf(" (exit: %d)", int(exitCode)))
			}
		}
	}

	return truncationStyle.Render(truncMsg) + "\n"
}

// formatSearchResults formats search results with match highlighting
func formatSearchResults(rich *RichMetadata) string {
	var result strings.Builder
	// Sanitize preview before splitting
	prev := safeText(rich.Preview)
	lines := strings.Split(prev, "\n")

	fileStyle := lipgloss.NewStyle().Bold(true)
	matchStyle := lipgloss.NewStyle() // No special formatting
	lineNumStyle := Styles.Help

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
func formatDirectoryListing(rich *RichMetadata) string {
	var result strings.Builder
	// Sanitize preview before splitting
	prev := safeText(rich.Preview)
	lines := strings.Split(prev, "\n")

	dirStyle := lipgloss.NewStyle().Bold(true) // Bold for directories
	fileStyle := lipgloss.NewStyle()           // Normal for files
	execStyle := lipgloss.NewStyle()           // Normal for executables

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
						Foreground(Styles.Help.GetForeground()).
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
			Foreground(Styles.Error.GetForeground()).
			Bold(true)
	default:
		// All other display types use bold only
		return "", lipgloss.NewStyle().Bold(true)
	}
}

// getGlamourRenderer returns a cached glamour renderer
func getGlamourRenderer() *glamour.TermRenderer {
	if glamourRenderer == nil {
		// Get the color from the current theme
		documentColor := "252" // default
		if CurrentTheme != nil && CurrentTheme.Colors.Semantic.AssistantMessage != "" {
			// Strip the # from hex colors for glamour
			documentColor = strings.TrimPrefix(CurrentTheme.Colors.Semantic.AssistantMessage, "#")
		}

		// Custom style based on dark theme but with no margins
		customStyle := fmt.Sprintf(`{
			"document": {
				"block_prefix": "",
				"block_suffix": "\n",
				"color": "%s",
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
		}`, documentColor)

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
	return renderMessageWithWidth(msg, debugMode, pulseFrame, approvalList, 0)
}

// renderMessageWithWidth renders a single message with width-aware formatting
func renderMessageWithWidth(msg Message, debugMode bool, pulseFrame int, approvalList *list.Model, termWidth int) string {
	switch msg.Role {
	case "user":
		return renderUserMessage(msg)
	case "assistant":
		return renderAssistantMessage(msg)
	case "tool_call":
		return renderToolCallWithWidth(msg, debugMode, pulseFrame, termWidth)
	case "tool_result":
		// Always show tool results, not just in debug mode
		return renderToolResultWithWidth(msg, termWidth)
	case "approval_request":
		if approvalList != nil {
			return renderApprovalRequestWithWidth(msg, *approvalList, termWidth)
		}
		return ""
	default:
		return ""
	}
}

// renderUserMessage renders a user message
func renderUserMessage(msg Message) string {
	// Use accent color for the prompt indicator
	prefix := Styles.MessagePrompt.Render("> ")
	content := msg.Content

	// Preserve explicit newlines by splitting first, then wrapping each line
	rawLines := strings.Split(content, "\n")
	var result strings.Builder
	firstOutLine := true

	for _, raw := range rawLines {
		// Preserve blank lines
		if raw == "" {
			if firstOutLine {
				result.WriteString(prefix)
				firstOutLine = false
			} else {
				result.WriteString("  ")
			}
			result.WriteString(userStyle.Render(""))
			result.WriteString("\n")
			continue
		}

		// Wrap each logical line independently to keep paragraph breaks
		wrapped := wrapText(raw, 80)
		if len(wrapped) == 0 {
			// Shouldn't happen, but keep a blank line
			if firstOutLine {
				result.WriteString(prefix)
				firstOutLine = false
			} else {
				result.WriteString("  ")
			}
			result.WriteString(userStyle.Render(""))
			result.WriteString("\n")
			continue
		}

		for i, line := range wrapped {
			if firstOutLine {
				result.WriteString(prefix)
				firstOutLine = false
			} else {
				result.WriteString("  ")
			}
			result.WriteString(userStyle.Render(line))
			result.WriteString("\n")
			// Subsequent wrapped parts of the same raw line will also be indented by two spaces
			_ = i // explicit no-op; retained for clarity
		}
	}

	// Add spacing after user message
	result.WriteString("\n")
	return result.String()
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
	return renderToolCallWithWidth(msg, debugMode, pulseFrame, 0)
}

// renderToolCallWithWidth renders a tool call with width-aware formatting
func renderToolCallWithWidth(msg Message, debugMode bool, pulseFrame int, termWidth int) string {
	if msg.ToolCall == nil {
		return ""
	}

	// Create pulsing dot for running tools
	var indicator string
	if msg.ToolCall.Running {
		// Tool is still running - create pulsing dot
		// Create a pulsing effect by varying the alpha/brightness
		// We'll create a gradient of colors from dim to bright
		baseHex := strings.TrimPrefix(CurrentTheme.Colors.Semantic.ToolName, "#")
		if baseHex == "" {
			baseHex = "bb9af7" // fallback
		}

		// Parse the base color
		baseColor, _ := colorful.Hex("#" + baseHex)
		h, s, l := baseColor.Hsl()

		// Create brightness variations for pulsing
		// Use sine wave for smooth pulsing (-1 to 1)
		pulseFactor := math.Sin(float64(pulseFrame) * math.Pi / 4.0)
		// Vary lightness ±20% around the original, capped at both ends
		newL := l + (0.2 * pulseFactor)
		newL = math.Max(0.2, math.Min(newL, 0.8)) // Keep between 20% and 80% lightness

		pulseColor := colorful.Hsl(h, s, newL)
		pulsingDot := lipgloss.NewStyle().Foreground(lipgloss.Color(pulseColor.Hex())).Render("⏺")
		indicator = pulsingDot
	} else {
		// Tool has completed - use static dot
		indicator = toolStyle.Render("⏺")
	}

	toolName := msg.ToolCall.Name

	// Calculate available width for arguments
	// Format: "⏺ toolName(args)"
	// Account for: indicator (2) + space (1) + tool name + "(" + ")"
	availableWidth := termWidth
	if availableWidth <= 0 {
		// Default width if not provided
		availableWidth = 80
	}
	// Subtract fixed elements: "⏺ " (2) + "(" (1) + ")" (1)
	availableWidth = availableWidth - 2 - len(toolName) - 2

	// Parse and format arguments with smart truncation
	formattedArgs := formatToolArgumentsSmart(msg.ToolCall.Arguments, availableWidth)
	// Sanitize to avoid control characters or ANSI sequences in the arguments display
	formattedArgs = safeText(formattedArgs)

	// Apply styling to tool name after calculation
	toolNameStyled := toolNameStyle.Render(toolName)

	// Start with spacing and add spacing at the end
	result := fmt.Sprintf("%s %s(%s)\n", indicator, toolNameStyled, formattedArgs)

	// Render result (plain or rich) when the tool is no longer running
	if !msg.ToolCall.Running {
		result += renderToolResultWithWidth(msg, termWidth)
	}

	// Add spacing after tool call
	return result + "\n"
}

// renderToolResult renders tool output
func renderToolResult(msg Message) string {
	if msg.ToolCall == nil {
		return ""
	}

	// Special handling for cancelled tools
	if msg.ToolCall.Result == "[Cancelled]" {
		cancelledStyle := lipgloss.NewStyle().
			Foreground(Styles.Warning.GetForeground()).
			Italic(true)
		return cancelledStyle.Render("  └─ Cancelled by user") + "\n"
	}

	// Check if we have rich metadata
	richMetadata := parseRichMetadata(msg.ToolCall.Metadata)

	// Use rich formatting if metadata is available
	if richMetadata != nil && richMetadata.DisplayType != "" {
		return renderRichToolResult(richMetadata, msg.ToolCall.Result)
	}

	// If no metadata and no plain result, nothing to render
	if msg.ToolCall.Result == "" {
		return ""
	}

	// Fallback to plain text rendering
	result := msg.ToolCall.Result
	// Strip ANSI and sanitize to avoid non-printable characters breaking the TUI
	result = safeText(result)

	// Truncate long results
	if len(result) > 500 {
		result = result[:500] + "..."
	}

	// Format as indented output with a more visible style
	lines := strings.Split(result, "\n")
	// Apply the same style to both the prefix and the content for consistency
	// Calculate available width (assume 80 char terminal - 5 spaces indent for └─ prefix)
	maxLineWidth := 75

	// Handle first line with the └─ prefix
	formatted := ""
	if len(lines) > 0 {
		firstLineWrapped := wrapLineHard(lines[0], maxLineWidth-3) // -3 for "└─ "
		if len(firstLineWrapped) > 0 {
			formatted = toolResultStyle.Render("  └─ "+firstLineWrapped[0]) + "\n"
			// Add any wrapped portions of the first line
			for j := 1; j < len(firstLineWrapped); j++ {
				formatted += toolResultStyle.Render("     "+firstLineWrapped[j]) + "\n"
			}
		}
	}

	// Handle remaining lines with wrapping
	linesShown := 1
	for i := 1; i < len(lines) && linesShown < 10; i++ {
		wrappedParts := wrapLineHard(lines[i], maxLineWidth)
		for _, part := range wrappedParts {
			if linesShown >= 10 {
				break
			}
			formatted += toolResultStyle.Render("     "+part) + "\n"
			linesShown++
		}
	}

	return formatted
}

// renderRichToolResult renders tool output in rich format
func renderRichToolResult(rich *RichMetadata, plainOutput string) string {
	var result strings.Builder

	// Get type-specific formatting
	icon, summaryStyle := getDisplayTypeStyle(rich.DisplayType)

	// Start with the summary line with type indicator
	result.WriteString("  └─ ")
	if icon != "" {
		result.WriteString(icon + " ")
	}

	// Use summary if available, otherwise use first line of output
	summary := rich.Summary
	if summary == "" {
		// Sanitize plain output before extracting a summary line
		plainOutput = safeText(plainOutput)
		lines := strings.Split(plainOutput, "\n")
		if len(lines) > 0 {
			summary = lines[0]
			if len(summary) > 80 {
				summary = summary[:77] + "..."
			}
		}
	}
	result.WriteString(summaryStyle.Render(safeText(summary)))
	result.WriteString("\n")

	// Add preview if available with type-specific formatting
	if rich.Preview != "" {
		// Apply type-specific preview formatting
		result.WriteString(formatRichPreview(rich))
	} else if plainOutput != "" {
		// If no preview, show a snippet of the plain output
		po := safeText(plainOutput)
		lines := strings.Split(po, "\n")
		for i, line := range lines {
			if i >= 5 { // Limit to 5 lines if no explicit preview
				if len(lines) > 5 {
					truncStyle := lipgloss.NewStyle().
						Foreground(Styles.Help.GetForeground()).
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

	return result.String()
}

// formatToolArguments formats tool arguments for display
func formatToolArguments(argsStr string) string {
	return formatToolArgumentsWithLimit(argsStr, 100)
}

// formatToolArgumentsSmart formats tool arguments with intelligent truncation
// It tries to keep the structure readable while fitting within the available width
func formatToolArgumentsSmart(argsStr string, availableWidth int) string {
	// Minimum width to show anything useful
	if availableWidth < 10 {
		return "..."
	}

	// Try to parse as JSON using orderedmap to preserve field order
	om := orderedmap.New()
	if err := json.Unmarshal([]byte(argsStr), om); err == nil {
		// Successfully parsed as JSON object
		parts := []string{}
		ellipsisLen := 3   // "..."
		commaSpaceLen := 2 // ", "

		// First pass: collect all formatted arguments
		allParts := []struct {
			key   string
			value string
			full  string // Full "key: value" string
		}{}

		for _, k := range om.Keys() {
			v, _ := om.Get(k)

			// Format value based on type
			var valueStr string
			switch val := v.(type) {
			case string:
				// Quote strings
				valueStr = fmt.Sprintf("%q", val)
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
				} else {
					valueStr = fmt.Sprintf("%v", val)
				}
			}

			fullArg := fmt.Sprintf("%s: %s", k, valueStr)
			allParts = append(allParts, struct {
				key   string
				value string
				full  string
			}{
				key:   k,
				value: valueStr,
				full:  fullArg,
			})
		}

		// Strategy: Include as many complete arguments as possible
		// If a single argument is too long, truncate its value
		currentLen := 0
		lastFullIndex := -1

		for i, part := range allParts {
			// Calculate length with comma if not first item
			argLen := len(part.full)
			if i > 0 {
				argLen += commaSpaceLen
			}

			// Check if we can fit this argument completely
			if currentLen+argLen <= availableWidth {
				parts = append(parts, part.full)
				currentLen += argLen
				lastFullIndex = i
			} else {
				// Can't fit this argument completely
				// Try to fit a truncated version of this argument
				remainingSpace := availableWidth - currentLen
				if i > 0 {
					remainingSpace -= commaSpaceLen
				}

				// Only truncate if we have reasonable space (key + ": " + at least some value + "...")
				minArgLen := len(part.key) + 2 + 4 + ellipsisLen // "key: X..."

				if remainingSpace >= minArgLen && i == 0 {
					// Truncate the first argument if it's the only one that fits
					maxValueLen := remainingSpace - len(part.key) - 2 - ellipsisLen
					if maxValueLen > 0 {
						truncatedValue := part.value
						if len(truncatedValue) > maxValueLen {
							// Handle quoted strings specially to keep quotes
							if strings.HasPrefix(truncatedValue, "\"") && strings.HasSuffix(truncatedValue, "\"") {
								// Truncate inside the quotes
								innerContent := truncatedValue[1 : len(truncatedValue)-1]
								if len(innerContent) > maxValueLen-2 { // Account for quotes
									truncatedValue = fmt.Sprintf("\"%s...\"", innerContent[:maxValueLen-5])
								}
							} else {
								truncatedValue = truncatedValue[:maxValueLen] + "..."
							}
						}
						parts = append(parts, fmt.Sprintf("%s: %s", part.key, truncatedValue))
					} else {
						// Just show ellipsis if we can't show anything useful
						parts = append(parts, "...")
					}
					break
				} else if lastFullIndex >= 0 && i > lastFullIndex {
					// We've already added some complete arguments
					// Add ellipsis to indicate there are more
					if currentLen+commaSpaceLen+ellipsisLen <= availableWidth {
						parts = append(parts, "...")
					}
					break
				} else if remainingSpace >= len(part.key)+6 {
					// Try to at least show "key: ..."
					parts = append(parts, fmt.Sprintf("%s: ...", part.key))
					break
				} else {
					// Can't fit anything useful, just use ellipsis
					if len(parts) > 0 {
						// We have some args already, add ellipsis
						if currentLen+commaSpaceLen+ellipsisLen <= availableWidth {
							parts = append(parts, "...")
						}
					} else {
						// Nothing fits, just show ellipsis
						parts = append(parts, "...")
					}
					break
				}
			}
		}

		return strings.Join(parts, ", ")
	}

	// Not JSON or failed to parse
	// Simple truncation for non-JSON arguments
	if len(argsStr) <= availableWidth {
		return argsStr
	}

	ellipsisLen := 3
	if availableWidth > ellipsisLen {
		return argsStr[:availableWidth-ellipsisLen] + "..."
	}

	return "..."
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

// wrapLineHard wraps a single line at character boundaries to fit within maxWidth
// Preserves exact content including spaces and special characters
func wrapLineHard(line string, maxWidth int) []string {
	if maxWidth <= 0 {
		return []string{line}
	}

	if len(line) <= maxWidth {
		return []string{line}
	}

	var wrapped []string
	for len(line) > 0 {
		if len(line) <= maxWidth {
			wrapped = append(wrapped, line)
			break
		}
		// Take maxWidth characters
		wrapped = append(wrapped, line[:maxWidth])
		line = line[maxWidth:]
	}

	return wrapped
}

// renderWelcome renders the welcome message
func renderWelcome(welcomeText string) string {
	if welcomeText == "" {
		return ""
	}

	welcomeStyle := lipgloss.NewStyle().
		Foreground(Styles.Help.GetForeground()).
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
	// Backwards-compatible wrapper: default to fixed width when no width is provided
	return renderApprovalRequestWithWidth(msg, approvalList, 0)
}

// renderApprovalRequestWithWidth renders an approval request with awareness of available content width
func renderApprovalRequestWithWidth(msg Message, approvalList list.Model, contentWidth int) string {
	if msg.Approval == nil {
		return ""
	}

	// Check for rich metadata and use appropriate renderer
	if msg.Approval.Metadata != nil {
		if displayType, ok := msg.Approval.Metadata["display_type"].(string); ok {
			switch displayType {
			case "diff":
				return renderDiffApprovalWithWidth(msg, approvalList, contentWidth)
			case "file_write":
				return renderFileWriteApprovalWithWidth(msg, approvalList, contentWidth)
			case "command":
				return renderCommandApprovalWithWidth(msg, approvalList, contentWidth)
			}
		}
	}

	// Default rendering for regular approvals
	return renderTextApprovalWithWidth(msg, approvalList, contentWidth)
}

// renderTextApproval renders a standard text-based approval
func renderTextApproval(msg Message, approvalList list.Model) string {
	return renderTextApprovalWithWidth(msg, approvalList, 0)
}

// renderTextApprovalWithWidth renders a standard text-based approval sized to available width
func renderTextApprovalWithWidth(msg Message, approvalList list.Model, contentWidth int) string {
	// Determine box width
	boxWidth := contentWidth
	if boxWidth <= 0 {
		boxWidth = 70 // fallback default to preserve previous behavior
	}
	// min-width removed to avoid overflow on narrow terminals

	// Create a custom box with all the information
	// Since the list component doesn't handle multi-line titles well
	boxStyle := lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(Styles.ApprovalTitle.GetForeground()).
		Padding(1, 2).
		Width(boxWidth - 6)

	titleStyle := lipgloss.NewStyle().
		Bold(true).
		Foreground(Styles.ApprovalTitle.GetForeground())

	argsStyle := lipgloss.NewStyle().
		Foreground(Styles.Help.GetForeground())

	var content strings.Builder

	// Title
	content.WriteString(titleStyle.Render(fmt.Sprintf("Tool Approval: %s", msg.Approval.Tool)))
	content.WriteString("\n\n")

	// Arguments - handle word wrapping properly
	// Compute inner content width (box width minus borders and padding)
	innerWidth := boxWidth - 6
	if innerWidth < 10 {
		innerWidth = 10
	}
	maxArgWidth := innerWidth
	argsLabel := "Arguments: "
	// Sanitize arguments to prevent control characters
	argsStr := safeText(msg.Approval.Arguments)

	// First line with label
	if len(argsLabel)+len(argsStr) <= maxArgWidth {
		// Fits on one line
		content.WriteString(argsStyle.Render(argsLabel + argsStr))
	} else {
		// Need to wrap
		remaining := argsStr
		firstLineLen := maxArgWidth - len(argsLabel)

		if len(remaining) > firstLineLen {
			// First line with label
			content.WriteString(argsStyle.Render(argsLabel + remaining[:firstLineLen]))
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

// renderDiffApproval renders an approval request with diff visualization
func renderDiffApproval(msg Message, approvalList list.Model) string {
	boxStyle := lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(Styles.ApprovalTitle.GetForeground()).
		Padding(1, 2).
		Width(80) // Wider for diffs

	titleStyle := lipgloss.NewStyle().
		Bold(true).
		Foreground(Styles.ApprovalTitle.GetForeground())

	// Styles for diff lines
	additionStyle := Styles.Info  // Use info color for additions
	deletionStyle := Styles.Error // Use error color for deletions
	contextStyle := Styles.Help   // Use help color for context

	var content strings.Builder

	// Title with file path if available
	title := fmt.Sprintf("Tool Approval: %s", msg.Approval.Tool)
	if metadata, ok := msg.Approval.Metadata["metadata"].(map[string]interface{}); ok {
		if filePath, ok := metadata["file_path"].(string); ok && filePath != "" {
			title = fmt.Sprintf("Tool Approval: %s - %s", msg.Approval.Tool, filePath)
		}
	}
	content.WriteString(titleStyle.Render(title))
	content.WriteString("\n\n")

	// Extract and render diff lines
	if value, ok := msg.Approval.Metadata["value"].(map[string]interface{}); ok {
		if diffLines, ok := value["diff_lines"].([]interface{}); ok && len(diffLines) > 0 {
			content.WriteString("Changes:\n")

			// Track line numbers for both old and new file
			oldLine := 0
			newLine := 0
			inHunk := false
			hunkCount := 0

			// Show all diff lines - hunking already limits to relevant changes
			for _, line := range diffLines {

				lineStr, _ := line.(string)
				// Strip trailing newlines - we'll manage line breaks ourselves
				lineStr = strings.TrimSuffix(lineStr, "\n")
				// Sanitize to avoid control characters or ANSI sequences
				lineStr = safeText(lineStr)

				// Skip file headers (---/+++) as they're redundant with the title
				if strings.HasPrefix(lineStr, "+++") || strings.HasPrefix(lineStr, "---") {
					continue
				}

				// Parse hunk headers to get line numbers
				if strings.HasPrefix(lineStr, "@@") {
					// Extract line numbers from hunk header
					if matches := regexp.MustCompile(`@@ -(\d+)(?:,\d+)? \+(\d+)(?:,(\d+))? @@`).FindStringSubmatch(lineStr); len(matches) > 0 {
						oldLine, _ = strconv.Atoi(matches[1])
						newLine, _ = strconv.Atoi(matches[2])

						// Add separator between hunks
						if hunkCount > 0 {
							content.WriteString(contextStyle.Render("\n  ...\n\n"))
						}

						inHunk = true
						hunkCount++
					}
				} else if inHunk {
					// Calculate max line width (box width - padding - borders - line number space)
					maxLineWidth := 80 - 4 - 2 - 7 // ~67 chars for content

					// Format diff lines with line numbers
					if strings.HasPrefix(lineStr, "+") && !strings.HasPrefix(lineStr, "+++") {
						// Addition lines - show new line number
						lineNumStr := fmt.Sprintf("%4d ", newLine)
						lineContent := strings.TrimPrefix(lineStr, "+")

						// Wrap long lines
						if len(lineContent) > maxLineWidth {
							content.WriteString(contextStyle.Render(lineNumStr))
							content.WriteString(additionStyle.Render("+ "))

							// Split and wrap the line
							for i := 0; i < len(lineContent); i += maxLineWidth {
								end := i + maxLineWidth
								if end > len(lineContent) {
									end = len(lineContent)
								}

								if i > 0 {
									// Continuation line - indent without line number
									content.WriteString("\n")
									content.WriteString(contextStyle.Render("     ")) // Space for line number
									content.WriteString(additionStyle.Render("  "))   // Space for +/- indicator
								}
								content.WriteString(additionStyle.Render(lineContent[i:end]))
							}
						} else {
							content.WriteString(contextStyle.Render(lineNumStr))
							content.WriteString(additionStyle.Render("+ " + lineContent))
						}
						newLine++
					} else if strings.HasPrefix(lineStr, "-") && !strings.HasPrefix(lineStr, "---") {
						// Deletion lines - show old line number
						lineNumStr := fmt.Sprintf("%4d ", oldLine)
						lineContent := strings.TrimPrefix(lineStr, "-")

						// Wrap long lines
						if len(lineContent) > maxLineWidth {
							content.WriteString(contextStyle.Render(lineNumStr))
							content.WriteString(deletionStyle.Render("- "))

							// Split and wrap the line
							for i := 0; i < len(lineContent); i += maxLineWidth {
								end := i + maxLineWidth
								if end > len(lineContent) {
									end = len(lineContent)
								}

								if i > 0 {
									// Continuation line - indent without line number
									content.WriteString("\n")
									content.WriteString(contextStyle.Render("     ")) // Space for line number
									content.WriteString(deletionStyle.Render("  "))   // Space for +/- indicator
								}
								content.WriteString(deletionStyle.Render(lineContent[i:end]))
							}
						} else {
							content.WriteString(contextStyle.Render(lineNumStr))
							content.WriteString(deletionStyle.Render("- " + lineContent))
						}
						oldLine++
					} else {
						// Context lines - show new line number
						lineNumStr := fmt.Sprintf("%4d ", newLine)

						// Wrap long lines
						if len(lineStr) > maxLineWidth {
							content.WriteString(contextStyle.Render(lineNumStr))
							content.WriteString(contextStyle.Render("  "))

							// Split and wrap the line
							for i := 0; i < len(lineStr); i += maxLineWidth {
								end := i + maxLineWidth
								if end > len(lineStr) {
									end = len(lineStr)
								}

								if i > 0 {
									// Continuation line - indent without line number
									content.WriteString("\n")
									content.WriteString(contextStyle.Render("     ")) // Space for line number
									content.WriteString(contextStyle.Render("  "))    // Space for +/- indicator
								}
								content.WriteString(contextStyle.Render(lineStr[i:end]))
							}
						} else {
							content.WriteString(contextStyle.Render(lineNumStr))
							content.WriteString(contextStyle.Render("  " + lineStr))
						}
						oldLine++
						newLine++
					}
					// Add newline after each diff line
					content.WriteString("\n")
				}
			}
		} else {
			// No diff lines available, fall back to showing arguments
			content.WriteString(contextStyle.Render("Arguments: " + safeText(msg.Approval.Arguments)))
		}
	} else {
		// No value metadata, show arguments
		content.WriteString(contextStyle.Render("Arguments: " + safeText(msg.Approval.Arguments)))
	}

	content.WriteString("\n")

	// Add the approval options from the list
	listView := approvalList.View()
	lines := strings.Split(listView, "\n")
	itemsStarted := false
	for _, line := range lines {
		// Normalize and skip any pre-item noise just like text approval
		if !itemsStarted && strings.TrimSpace(line) == "" {
			continue
		}
		if !itemsStarted && strings.TrimSpace(line) == "..." {
			continue
		}
		if !itemsStarted && strings.Contains(line, "Tool Approval:") {
			continue
		}
		if strings.Contains(line, "Approve") || strings.Contains(line, "Deny") {
			itemsStarted = true
			content.WriteString(line)
			content.WriteString("\n")
		} else if itemsStarted && strings.TrimSpace(line) != "" && !strings.Contains(line, "Tool Approval:") {
			content.WriteString(line)
			content.WriteString("\n")
		}
	}

	return boxStyle.Render(content.String()) + "\n\n"
}

// renderFileWriteApproval renders an approval request for file write operations
func renderFileWriteApproval(msg Message, approvalList list.Model) string {
	boxStyle := lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(Styles.ApprovalTitle.GetForeground()).
		Padding(1, 2).
		Width(75)

	titleStyle := lipgloss.NewStyle().
		Bold(true).
		Foreground(Styles.ApprovalTitle.GetForeground())

	infoStyle := Styles.Info    // Use info color
	contentStyle := Styles.Help // Use help color

	var content strings.Builder

	// Extract metadata
	summary := "File Write"
	if s, ok := msg.Approval.Metadata["summary"].(string); ok {
		summary = s
	}

	content.WriteString(titleStyle.Render(summary))
	content.WriteString("\n\n")

	// Show full content with syntax highlighting (not just preview)
	// First try to get the full content from the value field
	var codeContent string
	if value, ok := msg.Approval.Metadata["value"].(string); ok && value != "" {
		codeContent = value
		// Don't show "Content:" label - just show the content directly
	} else if preview, ok := msg.Approval.Metadata["preview"].(string); ok && preview != "" {
		// Fallback to preview if value is not available
		codeContent = preview
		content.WriteString(infoStyle.Render("Preview:"))
		content.WriteString("\n")
	}

	if codeContent != "" {
		// Detect language from file extension
		var language string
		if metadata, ok := msg.Approval.Metadata["metadata"].(map[string]interface{}); ok {
			if filePath, ok := metadata["file_path"].(string); ok {
				ext := filepath.Ext(filePath)
				switch ext {
				case ".go":
					language = "go"
				case ".py":
					language = "python"
				case ".js":
					language = "javascript"
				case ".ts":
					language = "typescript"
				case ".tsx", ".jsx":
					language = "typescript"
				case ".java":
					language = "java"
				case ".cpp", ".cc", ".cxx":
					language = "cpp"
				case ".c", ".h":
					language = "c"
				case ".rs":
					language = "rust"
				case ".rb":
					language = "ruby"
				case ".php":
					language = "php"
				case ".sh", ".bash":
					language = "bash"
				case ".yaml", ".yml":
					language = "yaml"
				case ".json":
					language = "json"
				case ".xml":
					language = "xml"
				case ".html", ".htm":
					language = "html"
				case ".css":
					language = "css"
				case ".scss", ".sass":
					language = "scss"
				case ".sql":
					language = "sql"
				case ".md", ".markdown":
					language = "markdown"
				case ".toml":
					language = "toml"
				case ".ini":
					language = "ini"
				case ".dockerfile", ".Dockerfile":
					language = "dockerfile"
				}
			}
		}

		// Sanitize content to prevent control characters from breaking TUI/highlighter
		codeContent = safeText(codeContent)

		// Sanitize content to prevent control characters from breaking TUI/highlighter
		codeContent = safeText(codeContent)

		// Use Chroma directly for syntax highlighting
		if language != "" {
			// Use a buffer to capture the highlighted output
			var buf bytes.Buffer

			// Use the same style as Glamour (charm) for consistency
			err := quick.Highlight(&buf, codeContent, language, "terminal256", "monokai")
			if err == nil {
				// Successfully highlighted - output the result
				highlightedContent := buf.String()
				// The output from Chroma should have correct newlines
				content.WriteString(highlightedContent)
				if !strings.HasSuffix(highlightedContent, "\n") {
					content.WriteString("\n")
				}
			} else {
				// Fallback to plain text if highlighting fails
				content.WriteString(contentStyle.Render(codeContent))
				if !strings.HasSuffix(codeContent, "\n") {
					content.WriteString("\n")
				}
			}
		} else {
			// No language detected or plain text file, use plain rendering
			content.WriteString(contentStyle.Render(codeContent))
			if !strings.HasSuffix(codeContent, "\n") {
				content.WriteString("\n")
			}
		}
	}

	content.WriteString("\n")

	// Add the approval options
	listView := approvalList.View()
	lines := strings.Split(listView, "\n")
	itemsStarted := false
	for _, line := range lines {
		// Normalize and skip any pre-item noise just like text approval
		if !itemsStarted && strings.TrimSpace(line) == "" {
			continue
		}
		if !itemsStarted && strings.TrimSpace(line) == "..." {
			continue
		}
		if !itemsStarted && strings.Contains(line, "Tool Approval:") {
			continue
		}
		if strings.Contains(line, "Approve") || strings.Contains(line, "Deny") {
			itemsStarted = true
			content.WriteString(line)
			content.WriteString("\n")
		} else if itemsStarted && strings.TrimSpace(line) != "" && !strings.Contains(line, "Tool Approval:") {
			content.WriteString(line)
			content.WriteString("\n")
		}
	}

	return boxStyle.Render(content.String()) + "\n\n"
}

// renderDiffApprovalWithWidth renders an approval request with diff visualization, sized to available width
func renderDiffApprovalWithWidth(msg Message, approvalList list.Model, contentWidth int) string {
	// Determine box width
	boxWidth := contentWidth
	if boxWidth <= 0 {
		boxWidth = 80 // fallback default
	}

	boxStyle := lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(Styles.ApprovalTitle.GetForeground()).
		Padding(1, 2).
		Width(boxWidth - 6)

	titleStyle := lipgloss.NewStyle().
		Bold(true).
		Foreground(Styles.ApprovalTitle.GetForeground())

	// Styles for diff lines
	additionStyle := Styles.Info  // Use info color for additions
	deletionStyle := Styles.Error // Use error color for deletions
	contextStyle := Styles.Help   // Use help color for context

	var content strings.Builder

	// Title with file path if available
	title := fmt.Sprintf("Tool Approval: %s", msg.Approval.Tool)
	if metadata, ok := msg.Approval.Metadata["metadata"].(map[string]interface{}); ok {
		if filePath, ok := metadata["file_path"].(string); ok && filePath != "" {
			title = fmt.Sprintf("Tool Approval: %s - %s", msg.Approval.Tool, filePath)
		}
	}
	content.WriteString(titleStyle.Render(title))
	content.WriteString("\n\n")

	// Extract and render diff lines
	if value, ok := msg.Approval.Metadata["value"].(map[string]interface{}); ok {
		if diffLines, ok := value["diff_lines"].([]interface{}); ok && len(diffLines) > 0 {
			content.WriteString("Changes:\n")

			// Track line numbers for both old and new file
			oldLine := 0
			newLine := 0
			inHunk := false
			hunkCount := 0

			// Show all diff lines - hunking already limits to relevant changes
			for _, line := range diffLines {
				lineStr, _ := line.(string)
				// Strip trailing newlines - we'll manage line breaks ourselves
				lineStr = strings.TrimSuffix(lineStr, "\n")

				// Skip file headers (---/+++) as they're redundant with the title
				if strings.HasPrefix(lineStr, "+++") || strings.HasPrefix(lineStr, "---") {
					continue
				}

				// Parse hunk headers to get line numbers
				if strings.HasPrefix(lineStr, "@@") {
					// Extract line numbers from hunk header
					if matches := regexp.MustCompile(`@@ -(\d+)(?:,\d+)? \+(\d+)(?:,(\d+))? @@`).FindStringSubmatch(lineStr); len(matches) > 0 {
						oldLine, _ = strconv.Atoi(matches[1])
						newLine, _ = strconv.Atoi(matches[2])

						// Add separator between hunks
						if hunkCount > 0 {
							content.WriteString(contextStyle.Render("\n  ...\n\n"))
						}

						inHunk = true
						hunkCount++
					}
				} else if inHunk {
					// Calculate max line width (box width - padding - borders - line number space)
					innerWidth := boxWidth - 6
					if innerWidth < 10 {
						innerWidth = 10
					}
					maxLineWidth := innerWidth - 7 // line number + indicator width

					// Format diff lines with line numbers
					if strings.HasPrefix(lineStr, "+") && !strings.HasPrefix(lineStr, "+++") {
						// Addition lines - show new line number
						lineNumStr := fmt.Sprintf("%4d ", newLine)
						lineContent := strings.TrimPrefix(lineStr, "+")

						// Wrap long lines
						if len(lineContent) > maxLineWidth {
							content.WriteString(contextStyle.Render(lineNumStr))
							content.WriteString(additionStyle.Render("+ "))

							// Split and wrap the line
							for i := 0; i < len(lineContent); i += maxLineWidth {
								end := i + maxLineWidth
								if end > len(lineContent) {
									end = len(lineContent)
								}

								if i > 0 {
									// Continuation line - indent without line number
									content.WriteString("\n")
									content.WriteString(contextStyle.Render("     ")) // Space for line number
									content.WriteString(additionStyle.Render("  "))   // Space for +/- indicator
								}
								content.WriteString(additionStyle.Render(lineContent[i:end]))
							}
						} else {
							content.WriteString(contextStyle.Render(lineNumStr))
							content.WriteString(additionStyle.Render("+ " + lineContent))
						}
						newLine++
					} else if strings.HasPrefix(lineStr, "-") && !strings.HasPrefix(lineStr, "---") {
						// Deletion lines - show old line number
						lineNumStr := fmt.Sprintf("%4d ", oldLine)
						lineContent := strings.TrimPrefix(lineStr, "-")

						// Wrap long lines
						if len(lineContent) > maxLineWidth {
							content.WriteString(contextStyle.Render(lineNumStr))
							content.WriteString(deletionStyle.Render("- "))

							// Split and wrap the line
							for i := 0; i < len(lineContent); i += maxLineWidth {
								end := i + maxLineWidth
								if end > len(lineContent) {
									end = len(lineContent)
								}

								if i > 0 {
									// Continuation line - indent without line number
									content.WriteString("\n")
									content.WriteString(contextStyle.Render("     ")) // Space for line number
									content.WriteString(deletionStyle.Render("  "))   // Space for +/- indicator
								}
								content.WriteString(deletionStyle.Render(lineContent[i:end]))
							}
						} else {
							content.WriteString(contextStyle.Render(lineNumStr))
							content.WriteString(deletionStyle.Render("- " + lineContent))
						}
						oldLine++
					} else {
						// Context lines - show new line number
						lineNumStr := fmt.Sprintf("%4d ", newLine)

						// Wrap long lines
						if len(lineStr) > maxLineWidth {
							content.WriteString(contextStyle.Render(lineNumStr))
							content.WriteString(contextStyle.Render("  "))

							// Split and wrap the line
							for i := 0; i < len(lineStr); i += maxLineWidth {
								end := i + maxLineWidth
								if end > len(lineStr) {
									end = len(lineStr)
								}

								if i > 0 {
									// Continuation line - indent without line number
									content.WriteString("\n")
									content.WriteString(contextStyle.Render("     ")) // Space for line number
									content.WriteString(contextStyle.Render("  "))    // Space for +/- indicator
								}
								content.WriteString(contextStyle.Render(lineStr[i:end]))
							}
						} else {
							content.WriteString(contextStyle.Render(lineNumStr))
							content.WriteString(contextStyle.Render("  " + lineStr))
						}
						oldLine++
						newLine++
					}
					// Add newline after each diff line
					content.WriteString("\n")
				}
			}
		} else {
			// No diff lines available, fall back to showing arguments
			content.WriteString(contextStyle.Render("Arguments: " + safeText(msg.Approval.Arguments)))
		}
	} else {
		// No value metadata, show arguments
		content.WriteString(contextStyle.Render("Arguments: " + safeText(msg.Approval.Arguments)))
	}

	content.WriteString("\n")

	// Add the approval options from the list
	listView := approvalList.View()
	lines := strings.Split(listView, "\n")
	itemsStarted := false
	for _, line := range lines {
		// Normalize and skip any pre-item noise just like text approval
		if !itemsStarted && strings.TrimSpace(line) == "" {
			continue
		}
		if !itemsStarted && strings.TrimSpace(line) == "..." {
			continue
		}
		if !itemsStarted && strings.Contains(line, "Tool Approval:") {
			continue
		}
		if strings.Contains(line, "Approve") || strings.Contains(line, "Deny") {
			itemsStarted = true
			content.WriteString(line)
			content.WriteString("\n")
		} else if itemsStarted && strings.TrimSpace(line) != "" && !strings.Contains(line, "Tool Approval:") {
			content.WriteString(line)
			content.WriteString("\n")
		}
	}

	return boxStyle.Render(content.String()) + "\n\n"
}

// renderFileWriteApprovalWithWidth renders an approval request for file write operations, sized to available width
func renderFileWriteApprovalWithWidth(msg Message, approvalList list.Model, contentWidth int) string {
	// Determine box width
	boxWidth := contentWidth
	if boxWidth <= 0 {
		boxWidth = 75 // fallback default
	}

	boxStyle := lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(Styles.ApprovalTitle.GetForeground()).
		Padding(1, 2).
		Width(boxWidth - 6)

	titleStyle := lipgloss.NewStyle().
		Bold(true).
		Foreground(Styles.ApprovalTitle.GetForeground())

	infoStyle := Styles.Info    // Use info color
	contentStyle := Styles.Help // Use help color

	var content strings.Builder

	// Extract metadata
	summary := "File Write"
	if s, ok := msg.Approval.Metadata["summary"].(string); ok {
		summary = s
	}

	content.WriteString(titleStyle.Render(summary))
	content.WriteString("\n\n")

	// Show full content with syntax highlighting (not just preview)
	// First try to get the full content from the value field
	var codeContent string
	if value, ok := msg.Approval.Metadata["value"].(string); ok && value != "" {
		codeContent = value
		// Don't show "Content:" label - just show the content directly
	} else if preview, ok := msg.Approval.Metadata["preview"].(string); ok && preview != "" {
		// Fallback to preview if value is not available
		codeContent = preview
		content.WriteString(infoStyle.Render("Preview:"))
		content.WriteString("\n")
	}

	if codeContent != "" {
		// Detect language from file extension
		var language string
		if metadata, ok := msg.Approval.Metadata["metadata"].(map[string]interface{}); ok {
			if filePath, ok := metadata["file_path"].(string); ok {
				ext := filepath.Ext(filePath)
				switch ext {
				case ".go":
					language = "go"
				case ".py":
					language = "python"
				case ".js":
					language = "javascript"
				case ".ts":
					language = "typescript"
				case ".tsx", ".jsx":
					language = "typescript"
				case ".java":
					language = "java"
				case ".cpp", ".cc", ".cxx":
					language = "cpp"
				case ".c", ".h":
					language = "c"
				case ".rs":
					language = "rust"
				case ".rb":
					language = "ruby"
				case ".php":
					language = "php"
				case ".sh", ".bash":
					language = "bash"
				case ".yaml", ".yml":
					language = "yaml"
				case ".json":
					language = "json"
				case ".xml":
					language = "xml"
				case ".html", ".htm":
					language = "html"
				case ".css":
					language = "css"
				case ".scss", ".sass":
					language = "scss"
				case ".sql":
					language = "sql"
				case ".md", ".markdown":
					language = "markdown"
				case ".toml":
					language = "toml"
				case ".ini":
					language = "ini"
				case ".dockerfile", ".Dockerfile":
					language = "dockerfile"
				}
			}
		}

		// Sanitize content to prevent control characters from breaking TUI/highlighter
		codeContent = safeText(codeContent)

		// Use Chroma directly for syntax highlighting
		if language != "" {
			// Use a buffer to capture the highlighted output
			var buf bytes.Buffer

			// Use the same style as Glamour (charm) for consistency
			err := quick.Highlight(&buf, codeContent, language, "terminal256", "monokai")
			if err == nil {
				// Successfully highlighted - output the result
				highlightedContent := buf.String()
				// The output from Chroma should have correct newlines
				content.WriteString(highlightedContent)
				if !strings.HasSuffix(highlightedContent, "\n") {
					content.WriteString("\n")
				}
			} else {
				// Fallback to plain text if highlighting fails
				content.WriteString(contentStyle.Render(codeContent))
				if !strings.HasSuffix(codeContent, "\n") {
					content.WriteString("\n")
				}
			}
		} else {
			// No language detected or plain text file, use plain rendering
			content.WriteString(contentStyle.Render(codeContent))
			if !strings.HasSuffix(codeContent, "\n") {
				content.WriteString("\n")
			}
		}
	}

	content.WriteString("\n")

	// Add the approval options
	listView := approvalList.View()
	lines := strings.Split(listView, "\n")
	itemsStarted := false
	for _, line := range lines {
		// Normalize and skip any pre-item noise just like text approval
		if !itemsStarted && strings.TrimSpace(line) == "" {
			continue
		}
		if !itemsStarted && strings.TrimSpace(line) == "..." {
			continue
		}
		if !itemsStarted && strings.Contains(line, "Tool Approval:") {
			continue
		}
		if strings.Contains(line, "Approve") || strings.Contains(line, "Deny") {
			itemsStarted = true
			content.WriteString(line)
			content.WriteString("\n")
		} else if itemsStarted && strings.TrimSpace(line) != "" && !strings.Contains(line, "Tool Approval:") {
			content.WriteString(line)
			content.WriteString("\n")
		}
	}

	return boxStyle.Render(content.String()) + "\n\n"
}

// renderCommandApprovalWithWidth renders an approval request for shell commands (execute_bash)
func renderCommandApprovalWithWidth(msg Message, approvalList list.Model, contentWidth int) string {
	// Determine box width
	boxWidth := contentWidth
	if boxWidth <= 0 {
		boxWidth = 75 // fallback default
	}

	boxStyle := lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(Styles.ApprovalTitle.GetForeground()).
		Padding(1, 2).
		Width(boxWidth - 6)

	titleStyle := lipgloss.NewStyle().
		Bold(true).
		Foreground(Styles.ApprovalTitle.GetForeground())

	// Styles for inline text
	infoStyle := Styles.Info
	contentStyle := Styles.Help

	var b strings.Builder

	// Extract metadata
	summary := "Run Command"
	if s, ok := msg.Approval.Metadata["summary"].(string); ok && s != "" {
		summary = s
	}
	b.WriteString(titleStyle.Render(summary))
	b.WriteString("\n\n")

	// Determine the command to render
	var cmd string
	if meta, ok := msg.Approval.Metadata["metadata"].(map[string]interface{}); ok {
		if c, ok := meta["command"].(string); ok {
			cmd = c
		}
	}
	if cmd == "" {
		// Fallback: attempt to parse from arguments JSON when metadata missing
		cmd = safeText(msg.Approval.Arguments)
	}

	// Render command as a highlighted bash line
	if cmd != "" {
		// Sanitize command to prevent control sequences
		safeCmd := safeText(cmd)
		code := "$ " + safeCmd
		var buf bytes.Buffer
		if err := quick.Highlight(&buf, code, "bash", "terminal256", "monokai"); err == nil {
			highlighted := buf.String()
			// Indent each rendered line to align with content
			for _, line := range strings.Split(strings.TrimRight(highlighted, "\n"), "\n") {
				b.WriteString("     ")
				b.WriteString(line)
				b.WriteString("\n")
			}
		} else {
			// Fallback to plain styled text
			b.WriteString(contentStyle.Render("     $ " + safeCmd))
			b.WriteString("\n")
		}
	} else {
		// If no command found, show the raw arguments as a fallback
		b.WriteString(infoStyle.Render("Arguments: "))
		b.WriteString(contentStyle.Render(safeText(msg.Approval.Arguments)))
		b.WriteString("\n")
	}

	b.WriteString("\n")

	// Add the approval options
	listView := approvalList.View()
	lines := strings.Split(listView, "\n")
	itemsStarted := false
	for _, line := range lines {
		if !itemsStarted && strings.TrimSpace(line) == "" {
			continue
		}
		if !itemsStarted && strings.TrimSpace(line) == "..." {
			continue
		}
		if !itemsStarted && strings.Contains(line, "Tool Approval:") {
			continue
		}
		if strings.Contains(line, "Approve") || strings.Contains(line, "Deny") {
			itemsStarted = true
			b.WriteString(line)
			b.WriteString("\n")
		} else if itemsStarted && strings.TrimSpace(line) != "" && !strings.Contains(line, "Tool Approval:") {
			b.WriteString(line)
			b.WriteString("\n")
		}
	}

	return boxStyle.Render(b.String()) + "\n\n"
}

// Width-aware rich preview formatter
func formatRichPreviewWithWidth(rich *RichMetadata, termWidth int) string {
	var result strings.Builder
	// Remove ANSI and sanitize preview before splitting
	prev := safeText(rich.Preview)
	previewLines := strings.Split(prev, "\n")
	maxPreviewLines := 10

	// Choose preview style based on display type
	var previewStyle lipgloss.Style
	switch rich.DisplayType {
	case "error":
		// Error output in red
		previewStyle = lipgloss.NewStyle().
			Foreground(Styles.Error.GetForeground())
	case "diff":
		// Special diff rendering uses value.diff_lines if available
		return renderDiffToolPreviewWithWidth(rich, termWidth)
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

	// Calculate available content width from terminal width (indent is 5 spaces)
	available := termWidth
	if available <= 0 {
		available = 80
	}
	maxLineWidth := available - 5
	if maxLineWidth < 10 {
		maxLineWidth = 10
	}

	linesShown := 0
	for _, line := range previewLines {
		if linesShown >= maxPreviewLines {
			break
		}
		wrappedParts := wrapLineHard(line, maxLineWidth)
		for _, part := range wrappedParts {
			if linesShown >= maxPreviewLines {
				break
			}
			result.WriteString(previewStyle.Render("     " + part))
			result.WriteString("\n")
			linesShown++
		}
	}

	if rich.Truncated {
		result.WriteString(formatTruncationIndicator(rich, len(previewLines), maxPreviewLines))
	}

	return result.String()
}

// Width-aware rich tool result renderer
func renderRichToolResultWithWidth(rich *RichMetadata, plainOutput string, termWidth int) string {
    var result strings.Builder

	icon, summaryStyle := getDisplayTypeStyle(rich.DisplayType)

	result.WriteString("  └─ ")
	if icon != "" {
		result.WriteString(icon + " ")
	}

	summary := rich.Summary
	if summary == "" {
		// Sanitize plain output before extracting a summary line
		plainOutput = safeText(plainOutput)
		lines := strings.Split(plainOutput, "\n")
		if len(lines) > 0 {
			summary = lines[0]
			if len(summary) > 80 {
				summary = summary[:77] + "..."
			}
		}
	}
	// Sanitize summary to prevent control characters from breaking the UI
	result.WriteString(summaryStyle.Render(safeText(summary)))
	result.WriteString("\n")

	// Special-case rich diff rendering for tool results
    if rich.DisplayType == "diff" {
        result.WriteString(renderDiffToolPreviewWithWidth(rich, termWidth))
        return result.String()
    }

    if rich.DisplayType == "table" {
        result.WriteString(renderTableRichWithWidth(rich, termWidth))
        return result.String()
    }

	if rich.Preview != "" {
		result.WriteString(formatRichPreviewWithWidth(rich, termWidth))
	} else if plainOutput != "" {
		// Ensure plain output is safe if used as a fallback
		po := safeText(plainOutput)
		lines := strings.Split(po, "\n")
		for i, line := range lines {
			if i >= 5 {
				if len(lines) > 5 {
					truncStyle := lipgloss.NewStyle().
						Foreground(Styles.Help.GetForeground()).
						Italic(true)
					result.WriteString(truncStyle.Render(fmt.Sprintf("     ... +%d more lines", len(lines)-5)))
					result.WriteString("\n")
				}
				break
			}
			available := termWidth
			if available <= 0 {
				available = 80
			}
			contentWidth := available - 5
			if contentWidth < 10 {
				contentWidth = 10
			}
			wrapped := wrapLineHard(line, contentWidth)
			for _, part := range wrapped {
				result.WriteString(toolResultStyle.Render("     " + part))
				result.WriteString("\n")
			}
		}
	}

	return result.String()
}

// renderTableRichWithWidth renders a tabular rich payload using bubbles/table
func renderTableRichWithWidth(rich *RichMetadata, termWidth int) string {
    available := termWidth
    if available <= 0 {
        available = 80
    }
    // Account for left margin (5) and outer border horizontal frame (left+right)
    outerFrame := lipgloss.NewStyle().BorderStyle(lipgloss.NormalBorder()).GetHorizontalFrameSize()
    contentWidth := available - 5 - outerFrame
    if contentWidth < 20 {
        var b strings.Builder
        if rich != nil && rich.Table != nil {
            if rows, ok := rich.Table["rows"].([]interface{}); ok {
                shown := 0
                for _, r := range rows {
                    if arr, ok := r.([]interface{}); ok {
                        parts := make([]string, 0, len(arr))
                        for _, c := range arr {
                            parts = append(parts, safeText(fmt.Sprint(c)))
                        }
                        b.WriteString(Styles.AssistantMessage.Render("     " + strings.Join(parts, ", ")))
                        b.WriteString("\n")
                        shown++
                        if shown >= 10 {
                            break
                        }
                    }
                }
                if rich.Truncated {
                    b.WriteString(formatTruncationIndicator(rich, shown, shown))
                }
            }
        }
        return b.String()
    }

    var cols []table.Column
    ncols := 0
    if rich != nil && rich.Table != nil {
        if rawCols, ok := rich.Table["columns"].([]interface{}); ok {
            for _, rc := range rawCols {
                if m, ok := rc.(map[string]interface{}); ok {
                    title := safeText(fmt.Sprint(m["title"]))
                    width := 0
                    if w, ok := m["width"].(float64); ok {
                        width = int(w)
                    }
                    cols = append(cols, table.Column{Title: title, Width: width})
                }
            }
        }
    }
    ncols = len(cols)

    var rows []table.Row
    if rich != nil && rich.Table != nil {
        if rawRows, ok := rich.Table["rows"].([]interface{}); ok {
            for _, rr := range rawRows {
                if arr, ok := rr.([]interface{}); ok {
                    r := make(table.Row, 0, len(arr))
                    for _, c := range arr {
                        r = append(r, safeText(fmt.Sprint(c)))
                    }
                    rows = append(rows, r)
                }
            }
        }
    }

    height := 10
    if rich != nil && rich.Table != nil {
        if h, ok := rich.Table["height"].(float64); ok && int(h) > 0 {
            height = int(h)
        }
    }
    if height > len(rows) {
        height = len(rows)
    }
    if height < 3 {
        height = 3
    }

    padPerCol := 2
    inner := contentWidth - padPerCol*ncols
    if inner < ncols*3 {
        inner = ncols * 3
    }
    sum := 0
    allHave := true
    for _, c := range cols {
        if c.Width <= 0 {
            allHave = false
        } else {
            sum += c.Width
        }
    }
    if ncols > 0 {
        if allHave {
            if sum > inner {
                scaled := make([]int, ncols)
                total := 0
                for i, c := range cols {
                    w := int(float64(c.Width) * float64(inner) / float64(sum))
                    if w < 3 {
                        w = 3
                    }
                    scaled[i] = w
                    total += w
                }
                rem := inner - total
                for i := 0; i < rem && i < ncols; i++ {
                    scaled[i]++
                }
                for i := range cols {
                    cols[i].Width = scaled[i]
                }
            }
        } else {
            base := inner / ncols
            if base < 3 {
                base = 3
            }
            rem := inner - base*ncols
            for i := range cols {
                cols[i].Width = base
                if rem > 0 {
                    cols[i].Width++
                    rem--
                }
            }
        }
    }

    st := table.DefaultStyles()
    st.Header = Styles.Header.Copy().
        Padding(0, 1).
        BorderStyle(lipgloss.NormalBorder()).
        BorderForeground(Styles.Header.GetForeground()).
        BorderBottom(true).
        Bold(false)
    st.Cell = Styles.AssistantMessage.Copy().Padding(0, 1)
    st.Selected = lipgloss.NewStyle()

    m := table.New(
        table.WithColumns(cols),
        table.WithRows(rows),
        table.WithWidth(contentWidth),
        table.WithHeight(height),
        table.WithFocused(false),
        table.WithStyles(st),
    )

    base := lipgloss.NewStyle().
        BorderStyle(lipgloss.NormalBorder()).
        BorderForeground(Styles.Header.GetForeground())
    block := base.Render(m.View())
    var b strings.Builder
    b.WriteString(lipgloss.NewStyle().MarginLeft(5).Render(block))
    b.WriteString("\n")

    if rich != nil && rich.Truncated {
        shown := height
        if shown < 0 {
            shown = 0
        }
        if meta, ok := rich.Metadata["total_rows"].(float64); ok {
            total := int(meta)
            if total > shown {
                msg := fmt.Sprintf("... +%d more rows", total-shown)
                line := lipgloss.NewStyle().Foreground(Styles.Help.GetForeground()).Italic(true).Render(msg)
                b.WriteString(lipgloss.NewStyle().MarginLeft(5).Render(line))
                b.WriteString("\n")
            }
        } else {
            ind := formatTruncationIndicator(rich, shown, shown)
            b.WriteString(lipgloss.NewStyle().MarginLeft(5).Render(strings.TrimRight(ind, "\n")))
            b.WriteString("\n")
        }
    }

    return b.String()
}

// renderDiffToolPreviewWithWidth renders a unified diff contained in rich.Value["diff_lines"]
// within the tool result area, sized to the available terminal width
func renderDiffToolPreviewWithWidth(rich *RichMetadata, termWidth int) string {
	// Styles for diff lines
	additionStyle := Styles.Info  // additions
	deletionStyle := Styles.Error // deletions
	contextStyle := Styles.Help   // context and line numbers

	// Determine available content width for lines (indent is 5 spaces "     ")
	available := termWidth
	if available <= 0 {
		available = 80
	}
	contentWidth := available - 5
	if contentWidth < 10 {
		contentWidth = 10
	}

	var b strings.Builder

	// Extract diff lines from value payload
	var diffLines []string
	if rich != nil && rich.Value != nil {
		if raw, ok := rich.Value["diff_lines"].([]interface{}); ok {
			for _, v := range raw {
				if s, ok := v.(string); ok {
					// Ensure we don't carry trailing newlines since we manage them ourselves
					diffLines = append(diffLines, strings.TrimSuffix(s, "\n"))
				}
			}
		} else if rawStrs, ok := rich.Value["diff_lines"].([]string); ok {
			for _, s := range rawStrs {
				diffLines = append(diffLines, strings.TrimSuffix(s, "\n"))
			}
		}
	}

	if len(diffLines) == 0 {
		// Fallback to preview text if value payload isn't available
		if rich.Preview != "" {
			return formatRichPreviewWithWidth(rich, termWidth)
		}
		return ""
	}

	// Track line numbers for both old and new files
	oldLine := 0
	newLine := 0
	inHunk := false

	// Iterate and render diff lines
	for _, lineStr := range diffLines {
		// Sanitize entire diff line before processing prefixes
		lineStr = safeText(lineStr)
		// Skip file header lines
		if strings.HasPrefix(lineStr, "+++") || strings.HasPrefix(lineStr, "---") {
			continue
		}

		if strings.HasPrefix(lineStr, "@@") {
			// Parse hunk header to reset line numbers
			if matches := regexp.MustCompile(`@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@`).FindStringSubmatch(lineStr); len(matches) > 0 {
				oldLine, _ = strconv.Atoi(matches[1])
				newLine, _ = strconv.Atoi(matches[2])
				inHunk = true
				// Add an empty spacer line between hunks for readability
				b.WriteString(contextStyle.Render("     " + lineStr))
				b.WriteString("\n")
			}
			continue
		}

		if !inHunk {
			// Before first hunk header, just print context lines indented
			wrapped := wrapLineHard(lineStr, contentWidth)
			for i, part := range wrapped {
				if i == 0 {
					b.WriteString(contextStyle.Render("     " + part))
				} else {
					b.WriteString(contextStyle.Render("     " + part))
				}
				b.WriteString("\n")
			}
			continue
		}

		// Prepare line number gutter width (4 digits + space)
		// Compose per-line rendering with wrapping
		renderWrapped := func(prefixStyled string, lineNumStr string, content string, style lipgloss.Style) {
			// Sanitize the content to avoid control characters
			content = safeText(content)
			// available content per line considering number gutter and +/- indicator
			gutterWidth := 5 // e.g., "  42 "
			indicatorWidth := 2
			maxLine := contentWidth - gutterWidth - indicatorWidth
			if maxLine < 10 {
				maxLine = 10
			}
			parts := wrapLineHard(content, maxLine)
			for idx, part := range parts {
				if idx == 0 {
					b.WriteString(contextStyle.Render("     "))
					b.WriteString(contextStyle.Render(lineNumStr))
					b.WriteString(prefixStyled)
					b.WriteString(style.Render(part))
				} else {
					b.WriteString(contextStyle.Render("     "))
					b.WriteString(contextStyle.Render(strings.Repeat(" ", len(lineNumStr))))
					b.WriteString(style.Render(strings.Repeat(" ", indicatorWidth)))
					b.WriteString(style.Render(part))
				}
				b.WriteString("\n")
			}
		}

		if strings.HasPrefix(lineStr, "+") && !strings.HasPrefix(lineStr, "+++") {
			// Addition: show new line number
			ln := fmt.Sprintf("%4d ", newLine)
			content := strings.TrimPrefix(lineStr, "+")
			renderWrapped(additionStyle.Render("+ "), ln, content, additionStyle)
			newLine++
		} else if strings.HasPrefix(lineStr, "-") && !strings.HasPrefix(lineStr, "---") {
			// Deletion: show old line number
			ln := fmt.Sprintf("%4d ", oldLine)
			content := strings.TrimPrefix(lineStr, "-")
			renderWrapped(deletionStyle.Render("- "), ln, content, deletionStyle)
			oldLine++
		} else {
			// Context line: advance both counters
			ln := fmt.Sprintf("%4d ", newLine)
			renderWrapped(contextStyle.Render("  "), ln, lineStr, contextStyle)
			oldLine++
			newLine++
		}
	}

	return b.String()
}

// Width-aware tool result renderer used by callers that know the viewport width
func renderToolResultWithWidth(msg Message, termWidth int) string {
	if msg.ToolCall == nil {
		return ""
	}

	// Special handling for cancelled tools
	if msg.ToolCall.Result == "[Cancelled]" {
		cancelledStyle := lipgloss.NewStyle().
			Foreground(Styles.Warning.GetForeground()).
			Italic(true)
		return cancelledStyle.Render("  └─ Cancelled by user") + "\n"
	}

	// Check if we have rich metadata
	richMetadata := parseRichMetadata(msg.ToolCall.Metadata)
	if richMetadata != nil && richMetadata.DisplayType != "" {
		return renderRichToolResultWithWidth(richMetadata, msg.ToolCall.Result, termWidth)
	}

	// If no metadata and no plain result, nothing to render
	if msg.ToolCall.Result == "" {
		return ""
	}

	// Fallback to plain text rendering
	result := msg.ToolCall.Result
	// Strip ANSI and sanitize to avoid non-printable characters breaking the TUI
	result = safeText(result)
	if len(result) > 500 {
		result = result[:500] + "..."
	}

	lines := strings.Split(result, "\n")
	available := termWidth
	if available <= 0 {
		available = 80
	}
	// Indent is 5 spaces for continuation lines; the first line has "  └─ " prefix
	contentWidth := available - 5
	if contentWidth < 10 {
		contentWidth = 10
	}

	formatted := ""
	if len(lines) > 0 {
		firstLineWrapped := wrapLineHard(lines[0], contentWidth)
		if len(firstLineWrapped) > 0 {
			formatted = toolResultStyle.Render("  └─ "+firstLineWrapped[0]) + "\n"
			for j := 1; j < len(firstLineWrapped); j++ {
				formatted += toolResultStyle.Render("     "+firstLineWrapped[j]) + "\n"
			}
		}
	}

	linesShown := 1
	for i := 1; i < len(lines) && linesShown < 10; i++ {
		wrappedParts := wrapLineHard(lines[i], contentWidth)
		for _, part := range wrappedParts {
			if linesShown >= 10 {
				break
			}
			formatted += toolResultStyle.Render("     "+part) + "\n"
			linesShown++
		}
	}

	return formatted
}
