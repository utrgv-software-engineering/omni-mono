package main

import (
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/help"
	"github.com/charmbracelet/bubbles/key"
	"github.com/charmbracelet/bubbles/list"
	"github.com/charmbracelet/bubbles/paginator"
	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/lucasb-eyer/go-colorful"
)

// AppState represents the current state of the application
type AppState int

const (
	StateConnecting AppState = iota
	StateReady
	StateWaitingForResponse
	StateApprovalRequired
	StateError
	StateSessionList
)

// keyMap defines keybindings for the application
type keyMap struct {
	Enter  key.Binding
	Up     key.Binding
	Down   key.Binding
	Left   key.Binding
	Right  key.Binding
	Tab    key.Binding
	Escape key.Binding
	Quit   key.Binding
}

// ShortHelp returns keybindings to be shown in the mini help view
func (k keyMap) ShortHelp() []key.Binding {
	bindings := []key.Binding{}
	
	// Show context-specific help based on state
	// This will be populated in the View method based on current state
	return bindings
}

// FullHelp returns keybindings for the expanded help view
func (k keyMap) FullHelp() [][]key.Binding {
	return [][]key.Binding{
		{k.Enter, k.Tab},
		{k.Up, k.Down, k.Left, k.Right},
		{k.Escape, k.Quit},
	}
}

var keys = keyMap{
	Enter: key.NewBinding(
		key.WithKeys("enter"),
		key.WithHelp("enter", "send message"),
	),
	Up: key.NewBinding(
		key.WithKeys("up"),
		key.WithHelp("↑", "previous"),
	),
	Down: key.NewBinding(
		key.WithKeys("down"),
		key.WithHelp("↓", "next"),
	),
	Left: key.NewBinding(
		key.WithKeys("left"),
		key.WithHelp("←", "prev page"),
	),
	Right: key.NewBinding(
		key.WithKeys("right"),
		key.WithHelp("→", "next page"),
	),
	Tab: key.NewBinding(
		key.WithKeys("tab"),
		key.WithHelp("tab", "new session"),
	),
	Escape: key.NewBinding(
		key.WithKeys("esc"),
		key.WithHelp("esc", "cancel"),
	),
	Quit: key.NewBinding(
		key.WithKeys("ctrl+c"),
		key.WithHelp("ctrl+c", "quit"),
	),
}

// Model represents the application state
type Model struct {
	// Core components
	rpcClient    *RPCClient
	sessionID    string
	currentRunID string

	// UI components
	input        CustomTextarea
	viewport     viewport.Model
	spinner      spinner.Model
	paginator    paginator.Model
	approvalList list.Model
	help         help.Model
	keys         keyMap
	messages     []Message

	// State
	state             AppState
	pendingTool       *ToolApprovalRequestEvent
	availableSessions []Session
	selectedSession   int  // Index within current page (0 to perPage-1)
	selectedIndex     int  // Absolute index across all sessions

	// Settings (from AgentSpec)
	agentName   string
	welcomeText string
	useSafeMode bool

	// Display
	width  int
	height int
	err    error

	// Channel for sending messages to the program
	msgChan chan tea.Msg

	// Debug mode
	debugMode bool
	
	// Resume mode - whether to show session list on startup
	resumeMode bool
	
	// Pulse animation state
	pulseFrame int
	pulseTimer int
}

// Message represents a chat message
type Message struct {
	Role     string // "user", "assistant", "tool_call", "tool_result", "approval_request"
	Content  string
	ToolCall *ToolCallInfo
	Approval *ToolApprovalInfo  // For approval_request messages
}

// ToolApprovalInfo contains approval request details
type ToolApprovalInfo struct {
	ApprovalID string
	Tool       string
	Arguments  string
	Metadata   map[string]interface{} // Rich metadata for approval UI
}

// ToolCallInfo contains tool call details
type ToolCallInfo struct {
	Name      string
	Arguments string
	CallID    string
	Result    string
	Metadata  map[string]interface{} // Rich output metadata
	Running   bool                   // Track if tool is currently executing
}

// NewModel creates a new application model
func NewModel(client *RPCClient, agentName string, welcomeText string, safeMode bool, sessionID string, resumeMode bool) Model {
	// Initialize custom textarea
	ta := NewCustomTextarea()
	ta.Placeholder = "Type your message..."
	ta.Focus()
	ta.CharLimit = 0  // No limit on characters
	ta.SetWidth(80)  // Initial width
	ta.SetHeight(1)  // Start with 1 line visible
	// MaxHeight will be set dynamically based on available space
	ta.ShowLineNumbers = false
	
	// Use SetPromptFunc to only show prompt on first line
	// The textarea viewport will keep line 0 as the first visible line
	ta.SetPromptFunc(3, func(lineIdx int) string {
		if lineIdx == 0 {
			return " > "
		}
		return "   "  // Same width as prompt for alignment
	})
	
	// Set styles for focused state
	ta.FocusedStyle.Base = lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(Styles.InputBorder.GetForeground())
	ta.FocusedStyle.CursorLine = lipgloss.NewStyle()  // No background for cursor line
	ta.FocusedStyle.Placeholder = Styles.Help.Copy().UnsetBackground()
	ta.FocusedStyle.Prompt = Styles.InputPrompt.Copy().UnsetBackground()
	ta.FocusedStyle.Text = Styles.InputText.Copy().UnsetBackground()
	
	// Set styles for blurred state (same as focused for now)
	ta.BlurredStyle = ta.FocusedStyle
	
	// Customize key bindings to use Enter for send and Alt+Enter for newline
	ta.KeyMap.InsertNewline = key.NewBinding(
		key.WithKeys("alt+enter"),
		key.WithHelp("alt+enter", "new line"),
	)

	// Initialize viewport
	vp := viewport.New(80, 20)

	// Initialize spinner
	s := spinner.New()
	s.Spinner = spinner.Dot
	s.Style = Styles.Spinner

	// Initialize paginator
	p := paginator.New()
	p.Type = paginator.Dots
	p.PerPage = 10
	p.ActiveDot = Styles.Spinner.Render("•")
	p.InactiveDot = Styles.Help.Render("•")

	// Initialize help
	h := help.New()
	h.ShowAll = false // Start with compact help

	// Check debug mode from environment
	debug := os.Getenv("OMNI_DEBUG") == "1"

	return Model{
		rpcClient:   client,
		sessionID:   sessionID,
		agentName:   agentName,
		welcomeText: welcomeText,
		useSafeMode: safeMode,
		input:       ta,
		viewport:    vp,
		spinner:     s,
		paginator:   p,
		help:        h,
		keys:        keys,
		messages:    []Message{},
		state:       StateConnecting,
		msgChan:     make(chan tea.Msg, 100),
		debugMode:   debug,
		resumeMode:  resumeMode,
	}
}

// Init initializes the model and returns the initial command
func (m Model) Init() tea.Cmd {
	// Connect to server and set up event handlers
	return tea.Batch(
		Blink,
		m.spinner.Tick,
		m.connect(),
		// Don't start listening until after connection is established
		m.tickPulse(),
	)
}

// tickPulse creates a ticker for the pulse animation
func (m Model) tickPulse() tea.Cmd {
	return tea.Tick(time.Millisecond*100, func(t time.Time) tea.Msg {
		return PulseTickMsg{}
	})
}

// PulseTickMsg is sent when the pulse animation should update
type PulseTickMsg struct{}

// max function is now in textarea.go

// connect establishes connection to the RPC server
func (m Model) connect() tea.Cmd {
	return func() tea.Msg {
		if m.debugMode {
			fmt.Fprintf(os.Stderr, "[DEBUG] Setting up event handlers...\n")
		}
		
		// Set up event handlers
		send := func(msg interface{}) {
			if m.msgChan != nil {
				m.msgChan <- msg
			}
		}

		handlers := ParseEventHandlers(send)
		for method, handler := range handlers {
			m.rpcClient.RegisterHandler(method, handler)
		}
		
		if m.debugMode {
			fmt.Fprintf(os.Stderr, "[DEBUG] Registered %d event handlers\n", len(handlers))
			fmt.Fprintf(os.Stderr, "[DEBUG] Initiating connection...\n")
		}

		// Connect to server
		if err := m.rpcClient.Connect(); err != nil {
			if m.debugMode {
				fmt.Fprintf(os.Stderr, "[DEBUG] Connection failed: %v\n", err)
			}
			return ErrorMsg{Error: err}
		}
		
		if m.debugMode {
			fmt.Fprintf(os.Stderr, "[DEBUG] Connection successful, sending ConnectedMsg\n")
		}

		return ConnectedMsg{}
	}
}

// ChannelMsg wraps messages from the RPC channel
type ChannelMsg struct {
	Msg tea.Msg
}

// listenForMessages listens for messages from the RPC client
func (m Model) listenForMessages() tea.Cmd {
	return func() tea.Msg {
		if m.msgChan == nil {
			return nil
		}
		// Simply block on the channel - this is fine in bubbletea v1.x
		// The tea.Cmd is run in a goroutine by the framework
		msg := <-m.msgChan
		if m.debugMode {
			fmt.Fprintf(os.Stderr, "[DEBUG] listenForMessages: received message: %T\n", msg)
		}
		// Wrap the message so we know it came from the channel
		return ChannelMsg{Msg: msg}
	}
}

// Update handles messages and updates the model
func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	if m.debugMode {
		fmt.Fprintf(os.Stderr, "[DEBUG] Update called with message type: %T\n", msg)
	}
	var cmds []tea.Cmd
	var textareaAlreadyUpdated bool

	// Handle wrapped channel messages
	if channelMsg, ok := msg.(ChannelMsg); ok {
		// Unwrap the actual message
		msg = channelMsg.Msg
		// Re-schedule listening for the next message
		cmds = append(cmds, m.listenForMessages())
	}

	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch msg.Type {
		case tea.KeyCtrlC:
			// Ctrl+C always quits the application
			return m, tea.Quit
		
		case tea.KeyEsc:
			if m.state == StateApprovalRequired {
				// Deny the approval when Escape is pressed
				// Save approval ID before clearing
				approvalID := m.pendingTool.ApprovalID
				
				// Remove approval message from messages
				for i := len(m.messages) - 1; i >= 0; i-- {
					if m.messages[i].Role == "approval_request" {
						m.messages = append(m.messages[:i], m.messages[i+1:]...)
						break
					}
				}
				
				// Change state to waiting for response (agent continues)
				m.state = StateWaitingForResponse
				m.pendingTool = nil
				m.approvalList = list.Model{}  // Clear the list
				
				// Recalculate viewport height for new footer size
				headerLines := 0
				if len(m.messages) == 0 {
					headerLines = 5
					if m.sessionID != "" {
						headerLines = 7
					}
				}
				footerLines := m.input.Height() + 3  // Textarea height + help + spacing
				topSpacing := 2  // Always 2 lines of top spacing
				m.viewport.Height = m.height - headerLines - footerLines - topSpacing
				m.updateViewport()
				
				// Send denial response to server AND clear screen to force full redraw
				return m, tea.Batch(
					tea.ClearScreen,
					func() tea.Msg {
						// Send denial (approved=false, alwaysApprove=false)
						err := m.rpcClient.ToolApprovalResponse(approvalID, false, false)
						if err != nil {
							return ErrorMsg{Error: err}
						}
						return nil
					},
				)
			} else if m.state == StateWaitingForResponse && m.currentRunID != "" {
				// Cancel the current run when Escape is pressed during execution
				runID := m.currentRunID
				m.currentRunID = ""  // Clear immediately to prevent double cancellation
				
				// Mark any running tool calls as cancelled
				for i := len(m.messages) - 1; i >= 0; i-- {
					if m.messages[i].Role == "tool_call" && m.messages[i].ToolCall != nil {
						if m.messages[i].ToolCall.Result == "" {
							// Tool was still running, mark as cancelled
							m.messages[i].ToolCall.Result = "[Cancelled]"
							m.messages[i].ToolCall.Running = false
						}
					}
				}
				
				// Change state back to ready
				m.state = StateReady
				
				// Add a message indicating cancellation
				m.messages = append(m.messages, Message{
					Role:    "assistant",
					Content: "[Run cancelled by user]",
				})
				m.updateViewport()
				
				// Send stop_run command to server
				return m, func() tea.Msg {
					err := m.rpcClient.StopRun(runID)
					if err != nil {
						return ErrorMsg{Error: err}
					}
					return nil
				}
			}
			// In other states, ESC does nothing (no longer quits)

		case tea.KeyEnter:
			// Check if Alt is pressed for newline insertion
			if m.state == StateReady {
				if msg.Alt {
					// Alt+Enter: let textarea handle it for newline
					var cmd tea.Cmd
					m.input, cmd = m.input.Update(msg)
					cmds = append(cmds, cmd)
					textareaAlreadyUpdated = true
					return m, tea.Batch(cmds...)
				} else {
					// Regular Enter: send message
					return m.sendMessage()
				}
			} else if m.state == StateApprovalRequired {
				// Handle approval list selection
				return m.handleApprovalListChoice()
			} else if m.state == StateSessionList {
				// Select session
				return m.selectSession()
			}

		case tea.KeyUp:
			if m.state == StateSessionList && len(m.availableSessions) > 0 {
				// Move selection up within current page or go to previous page
				if m.selectedSession > 0 {
					m.selectedSession--
					m.selectedIndex--
				} else if m.paginator.Page > 0 {
					// Go to previous page and select last item
					m.paginator.PrevPage()
					start, end := m.paginator.GetSliceBounds(len(m.availableSessions))
					m.selectedSession = end - start - 1
					m.selectedIndex--
				}
				return m, nil
			}

		case tea.KeyDown:
			if m.state == StateSessionList && len(m.availableSessions) > 0 {
				// Move selection down within current page or go to next page
				start, end := m.paginator.GetSliceBounds(len(m.availableSessions))
				pageSize := end - start
				
				if m.selectedSession < pageSize-1 {
					m.selectedSession++
					m.selectedIndex++
				} else if m.paginator.Page < m.paginator.TotalPages-1 {
					// Go to next page and select first item
					m.paginator.NextPage()
					m.selectedSession = 0
					m.selectedIndex++
				}
				return m, nil
			}

		case tea.KeyLeft:
			if m.state == StateSessionList && m.paginator.Page > 0 {
				// Previous page
				m.paginator.PrevPage()
				// Keep relative position on page if possible
				start, end := m.paginator.GetSliceBounds(len(m.availableSessions))
				pageSize := end - start
				if m.selectedSession >= pageSize {
					m.selectedSession = pageSize - 1
				}
				m.selectedIndex = start + m.selectedSession
				return m, nil
			}

		case tea.KeyRight:
			if m.state == StateSessionList && m.paginator.Page < m.paginator.TotalPages-1 {
				// Next page
				m.paginator.NextPage()
				// Keep relative position on page if possible
				start, end := m.paginator.GetSliceBounds(len(m.availableSessions))
				pageSize := end - start
				if m.selectedSession >= pageSize {
					m.selectedSession = pageSize - 1
				}
				m.selectedIndex = start + m.selectedSession
				return m, nil
			}

		case tea.KeyTab:
			if m.state == StateSessionList {
				// Create new session
				m.sessionID = ""
				m.state = StateReady
				m.updateViewport()
				return m, nil
			}
		}

	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height

		// Update viewport and input width
		m.viewport.Width = msg.Width - 4  // Account for padding
		
		// Calculate viewport height based on current state
		headerLines := 0
		if len(m.messages) == 0 {
			headerLines = 5  // Header with ASCII art + newlines
			if m.sessionID != "" {
				headerLines = 7  // Add lines for session display
			}
		}
		// Calculate footer lines based on textarea height
		footerLines := m.input.Height() + 3  // Textarea height + help (2 lines) + spacing (1 line)
		if m.state == StateApprovalRequired {
			footerLines = 3  // Just help text when approval is in the message list
		}
		topSpacing := 2  // Always 2 lines of top spacing
		m.viewport.Height = msg.Height - headerLines - footerLines - topSpacing

		// Update textarea width
		// Footer no longer has padding, so full width minus border (2 chars)
		m.input.SetWidth(msg.Width - 2)

		// Update help width
		m.help.Width = msg.Width - 4

	case ConnectedMsg:
		if m.debugMode {
			fmt.Fprintf(os.Stderr, "[DEBUG] Received ConnectedMsg, changing state from %v to StateReady\n", m.state)
		}
		// Start listening for messages now that we're connected
		cmds = append(cmds, m.listenForMessages())
		
		// Check if we need to resume a session or list sessions
		if m.sessionID != "" {
			m.state = StateReady
			// TODO: Load session history
			m.updateViewport()
		} else if m.resumeMode {
			// Only list sessions if --resume flag was passed
			newModel, cmd := m.listSessions()
			return newModel, tea.Batch(append(cmds, cmd)...)
		} else {
			// Start with a new session
			m.state = StateReady
			m.updateViewport()
		}

	case DisconnectedMsg:
		m.state = StateError
		m.err = fmt.Errorf("disconnected from server")
		m.updateViewport()

	case ErrorMsg:
		m.state = StateError
		m.err = msg.Error
		m.updateViewport()

	case ToolCalledMsg:
		// Don't clear approval state - let user handle it
		m.messages = append(m.messages, Message{
			Role: "tool_call",
			ToolCall: &ToolCallInfo{
				Name:      msg.Tool,
				Arguments: msg.Input,
				CallID:    msg.CallID,
			},
		})
		m.updateViewport()

	case ToolResultMsg:
		// Find the corresponding tool call and update it
		for i := len(m.messages) - 1; i >= 0; i-- {
			if m.messages[i].Role == "tool_call" && m.messages[i].ToolCall != nil {
				if m.messages[i].ToolCall.CallID == msg.CallID {
					m.messages[i].ToolCall.Result = msg.Output
					m.messages[i].ToolCall.Metadata = msg.Metadata
					break
				}
			}
		}
		m.updateViewport()

	case MessageOutputMsg:
		// Don't clear approval state - let user handle it
		// Update or create assistant message
		if len(m.messages) > 0 && m.messages[len(m.messages)-1].Role == "assistant" {
			// Update existing assistant message
			m.messages[len(m.messages)-1].Content = msg.Content
		} else {
			// Create new assistant message
			m.messages = append(m.messages, Message{
				Role:    "assistant",
				Content: msg.Content,
			})
		}
		m.updateViewport()

	case ToolApprovalRequestMsg:
		m.state = StateApprovalRequired
		m.pendingTool = &ToolApprovalRequestEvent{
			ApprovalID: msg.ApprovalID,
			Tool:       msg.Tool,
			Arguments:  msg.Arguments,
			Metadata:   msg.Metadata,
		}
		// Create approval list (pass metadata for potential future use)
		m.approvalList = CreateApprovalList(msg.Tool, msg.Arguments)
		// Add approval message to the message list with metadata
		m.messages = append(m.messages, Message{
			Role: "approval_request",
			Approval: &ToolApprovalInfo{
				ApprovalID: msg.ApprovalID,
				Tool:       msg.Tool,
				Arguments:  msg.Arguments,
				Metadata:   msg.Metadata,
			},
		})
		m.updateViewport()

	case RunStartedMsg:
		// Save the run and session IDs
		m.currentRunID = msg.RunID
		if m.sessionID == "" && msg.SessionID != "" {
			m.sessionID = msg.SessionID
		}
		
	case RunEndMsg:
		// Only change state to Ready if not waiting for approval
		if m.state != StateApprovalRequired {
			m.state = StateReady
		}
		m.currentRunID = ""
		// Don't clear pendingTool or approvalList - let user handle approval
		m.updateViewport()
		
	case SessionsListedMsg:
		if msg.Error != nil || msg.Sessions == nil || len(msg.Sessions) == 0 {
			// No sessions or error, start with new session
			m.state = StateReady
			m.updateViewport()
		} else {
			// Show session list
			m.availableSessions = msg.Sessions
			m.selectedSession = 0
			m.selectedIndex = 0
			// Setup paginator
			m.paginator.SetTotalPages(len(msg.Sessions))
			m.paginator.Page = 0
			m.state = StateSessionList
		}
	}

	// Update viewport only with mouse events (not keyboard)
	// This prevents viewport keyboard shortcuts from interfering with text input
	var vpCmd tea.Cmd
	switch msg.(type) {
	case tea.MouseMsg, tea.WindowSizeMsg:
		// Only pass mouse and window size events to viewport
		m.viewport, vpCmd = m.viewport.Update(msg)
		cmds = append(cmds, vpCmd)
	}
	// Note: We don't pass keyboard events to prevent 'u', 'd', 'j', 'k' etc.
	// from triggering viewport scrolling when the user is trying to type

	// Update spinner
	var spinnerCmd tea.Cmd
	m.spinner, spinnerCmd = m.spinner.Update(msg)
	cmds = append(cmds, spinnerCmd)
	
	// If spinner ticked and we're showing a status, update the viewport content only
	if _, isSpinnerTick := msg.(spinner.TickMsg); isSpinnerTick {
		if m.state == StateWaitingForResponse || m.state == StateConnecting {
			m.updateViewportContent()  // Use content-only update to preserve scroll
		}
	}
	
	// Handle pulse animation tick
	if _, isPulseTick := msg.(PulseTickMsg); isPulseTick {
		// Check if any tool calls are running (no result yet)
		hasRunningTools := false
		for _, message := range m.messages {
			if message.Role == "tool_call" && message.ToolCall != nil && message.ToolCall.Result == "" {
				hasRunningTools = true
				break
			}
		}
		
		if hasRunningTools {
			// Advance pulse frame
			m.pulseFrame = (m.pulseFrame + 1) % 8  // 8 frames for smooth pulse
			m.updateViewportContent()  // Use content-only update to preserve scroll
		}
		
		// Continue the pulse animation
		cmds = append(cmds, m.tickPulse())
	}

	// Update text input (unless in approval mode or already updated)
	if m.state != StateApprovalRequired && !textareaAlreadyUpdated {
		var cmd tea.Cmd
		oldInputHeight := m.input.Height()
		m.input, cmd = m.input.Update(msg)
		
		// Smart auto-grow: always expand to fit content, with viewport constraints
		lineCount := m.input.LineCount()
		
		// Calculate max allowed height for textarea
		// Allow it to take up to 50% of screen height or 20 lines, whichever is smaller
		maxAllowedLines := m.height / 2
		if maxAllowedLines > 20 {
			maxAllowedLines = 20
		}
		// But ensure we leave at least 5 lines for the viewport
		minViewportLines := 5
		headerLines := 0
		if len(m.messages) == 0 {
			headerLines = 5
			if m.sessionID != "" {
				headerLines = 7
			}
		}
		// Maximum textarea height = total height - header - min viewport - help (3 lines) - top spacing (2)
		maxFromViewport := m.height - headerLines - minViewportLines - 3 - 2
		
		// Use the smaller of the two limits
		if maxFromViewport < maxAllowedLines {
			maxAllowedLines = maxFromViewport
		}
		
		// Ensure maxAllowedLines is at least 1
		if maxAllowedLines < 1 {
			maxAllowedLines = 1
		}
		
		// Set MaxHeight on the textarea so it knows to enable scrolling
		m.input.MaxHeight = maxAllowedLines
		
		// Set the height to either the line count or the maximum allowed
		if lineCount > maxAllowedLines {
			m.input.SetHeight(maxAllowedLines)
		} else {
			m.input.SetHeight(lineCount)
		}
		
		// If textarea height changed, recalculate viewport height
		if m.input.Height() != oldInputHeight {
			// Check if we're at the bottom before resizing
			wasAtBottom := m.viewport.AtBottom()
			
			headerLines := 0
			if len(m.messages) == 0 {
				headerLines = 5
				if m.sessionID != "" {
					headerLines = 7
				}
			}
			footerLines := m.input.Height() + 3  // Textarea height + help + spacing
			topSpacing := 2  // Always 2 lines of top spacing
			newViewportHeight := m.height - headerLines - footerLines - topSpacing
			
			// Ensure minimum viewport height of 5 lines
			minViewportHeight := 5
			if newViewportHeight < minViewportHeight {
				newViewportHeight = minViewportHeight
			}
			
			m.viewport.Height = newViewportHeight
			
			// If we were at the bottom, stay at the bottom
			// This will naturally push content up when needed
			if wasAtBottom {
				m.viewport.GotoBottom()
			}
		}
		
		cmds = append(cmds, cmd)
	} else if m.state == StateApprovalRequired {
		// Update approval list when in approval mode
		var cmd tea.Cmd
		m.approvalList, cmd = m.approvalList.Update(msg)
		cmds = append(cmds, cmd)
	}

	return m, tea.Batch(cmds...)
}

// View renders the UI
func (m Model) View() string {
	if m.state == StateError {
		return m.renderError()
	} else if m.state == StateConnecting {
		return m.renderConnecting()
	} else if m.state == StateSessionList {
		return m.renderSessionList()
	}

	// Main chat view with proper layout
	var header, middle, footer string
	
	// Header (only show if no messages sent yet)
	if len(m.messages) == 0 {
		header = m.renderHeader()
	}
	
	// Messages viewport
	middle = m.viewport.View()
	
	// Footer (input or help text)
	if m.state == StateApprovalRequired {
		// Show help text for approval navigation
		footer = m.getContextualHelp()
	} else {
		footer = m.renderInput()
	}
	
	// Calculate available height for the middle section
	topSpacing := 2  // Always 2 lines of top spacing
	availableHeight := m.height - topSpacing
	headerHeight := 0
	if header != "" {
		headerHeight = lipgloss.Height(header) + 1 // +1 for the newline after header
	}
	footerHeight := lipgloss.Height(footer) + 1 // +1 for the newline before footer
	middleHeight := availableHeight - headerHeight - footerHeight
	
	// Ensure middle section fills available space
	if middleHeight > 0 {
		middleStyle := lipgloss.NewStyle().
			Height(middleHeight).
			Width(m.width)
		middle = middleStyle.Render(middle)
	}
	
	// Apply horizontal padding to all sections
	paddingStyle := lipgloss.NewStyle().PaddingLeft(2).PaddingRight(2)
	
	// Build the final layout
	var content strings.Builder
	
	// Add top spacing - always 2 lines for consistent spacing
	content.WriteString("\n\n")
	
	if header != "" {
		content.WriteString(paddingStyle.Render(header))
		content.WriteString("\n")
	}
	content.WriteString(paddingStyle.Render(middle))
	content.WriteString("\n")
	content.WriteString(footer)  // No padding for footer - border aligns with messages
	
	return content.String()
}

// Helper methods for rendering

func (m Model) renderHeader() string {
	// Create ASCII art for the agent name
	asciiArt := m.generateASCIIArt(m.agentName)
	
	var result strings.Builder
	result.WriteString(asciiArt)
	
	// Add session ID underneath if present
	if m.sessionID != "" {
		sessionStyle := lipgloss.NewStyle().
			Foreground(Styles.SessionSubtitle.GetForeground()).
			Italic(true)
		sessionInfo := fmt.Sprintf("Session: %s", m.sessionID[:8])
		result.WriteString("\n")
		result.WriteString(sessionStyle.Render(sessionInfo))
	}
	
	result.WriteString("\n")
	return result.String()
}

// generateASCIIArt creates gradient-colored ASCII art for the agent name
func (m Model) generateASCIIArt(text string) string {
	// Simple ASCII art generator using block characters
	// For a more compact display, we'll use a simple large text style
	lines := m.createSimpleASCII(text)
	
	// Join all lines into a single string for the gradient
	fullText := strings.Join(lines, "\n")
	
	// Apply a horizontal gradient across the entire ASCII art
	// This creates a smooth left-to-right gradient effect
	return m.applyHorizontalGradient(fullText)
}

// applyHorizontalGradient applies a smooth horizontal gradient to ASCII art
func (m Model) applyHorizontalGradient(text string) string {
	lines := strings.Split(text, "\n")
	var result strings.Builder
	
	// Get gradient colors from theme
	startHex := "#bb9af7" // default fallback start color (purple)
	endHex := "#f7768e"   // default fallback end color (pink/red)
	
	if CurrentTheme != nil {
		// Use theme-defined gradient colors if available
		if CurrentTheme.Colors.UI.HeaderGradientStart != "" {
			startHex = CurrentTheme.Colors.UI.HeaderGradientStart
		} else if CurrentTheme.Colors.UI.Header != "" {
			// No gradient - use same color for both start and end
			startHex = CurrentTheme.Colors.UI.Header
		}
		
		if CurrentTheme.Colors.UI.HeaderGradientEnd != "" {
			endHex = CurrentTheme.Colors.UI.HeaderGradientEnd
		} else if CurrentTheme.Colors.UI.Header != "" {
			// No gradient - use same color for both start and end
			endHex = CurrentTheme.Colors.UI.Header
		}
	}
	
	// Parse the colors
	startColor, _ := colorful.Hex(startHex)
	endColor, _ := colorful.Hex(endHex)
	
	for lineIdx, line := range lines {
		runes := []rune(line)
		lineResult := ""
		
		for i, r := range runes {
			// Skip spaces - don't color them
			if r == ' ' {
				lineResult += " "
				continue
			}
			
			// Calculate position in gradient (0.0 to 1.0)
			position := float64(i) / float64(len(runes)-1)
			if len(runes) == 1 {
				position = 0.5
			}
			
			// Blend colors using Lab color space for perceptually uniform gradient
			blendedColor := startColor.BlendLab(endColor, position)
			
			// Convert to hex for lipgloss
			hexColor := blendedColor.Hex()
			
			style := lipgloss.NewStyle().
				Foreground(lipgloss.Color(hexColor)).
				Bold(true)
			
			lineResult += style.Render(string(r))
		}
		
		result.WriteString(lineResult)
		if lineIdx < len(lines)-1 {
			result.WriteString("\n")
		}
	}
	
	return result.String()
}

// createSimpleASCII creates simple block-letter ASCII art
func (m Model) createSimpleASCII(text string) []string {
	// Use solid block characters for a bold, modern look with 3 lines
	upper := strings.ToUpper(text)
	
	// Create 3-line ASCII art using block characters
	line1 := ""
	line2 := ""
	line3 := ""
	
	for _, char := range upper {
		switch char {
		case ' ':
			line1 += "   "
			line2 += "   "
			line3 += "   "
		case '0':
			line1 += "█▀█ "
			line2 += "█ █ "
			line3 += "▀▀▀ "
		case '1':
			line1 += "▄█ "
			line2 += " █ "
			line3 += " ▀ "
		case '2':
			line1 += "▀▀█ "
			line2 += "█▀▀ "
			line3 += "▀▀▀ "
		case '3':
			line1 += "▀▀█ "
			line2 += "▀▀█ "
			line3 += "▀▀▀ "
		case '4':
			line1 += "█ █ "
			line2 += "▀▀█ "
			line3 += "  ▀ "
		case '5':
			line1 += "█▀▀ "
			line2 += "▀▀█ "
			line3 += "▀▀▀ "
		case '6':
			line1 += "█   "
			line2 += "█▀█ "
			line3 += "▀▀▀ "
		case '7':
			line1 += "▀▀█ "
			line2 += " █▀ "
			line3 += " ▀  "
		case '8':
			line1 += "█▀█ "
			line2 += "█▀█ "
			line3 += "▀▀▀ "
		case '9':
			line1 += "█▀█ "
			line2 += "▀▀█ "
			line3 += "▀▀▀ "
		case 'A':
			line1 += "█▀█ "
			line2 += "█▀█ "
			line3 += "▀ ▀ "
		case 'B':
			line1 += "█▀▄ "
			line2 += "█▀▄ "
			line3 += "▀▀  "
		case 'C':
			line1 += "█▀▀ "
			line2 += "█   "
			line3 += "▀▀▀ "
		case 'D':
			line1 += "█▀▄ "
			line2 += "█ █ "
			line3 += "▀▀  "
		case 'E':
			line1 += "█▀▀ "
			line2 += "█▀▀ "
			line3 += "▀▀▀ "
		case 'F':
			line1 += "█▀▀ "
			line2 += "█▀  "
			line3 += "▀   "
		case 'G':
			line1 += "█▀▀ "
			line2 += "█ █ "
			line3 += "▀▀▀ "
		case 'H':
			line1 += "█ █ "
			line2 += "█▀█ "
			line3 += "▀ ▀ "
		case 'I':
			line1 += "█ "
			line2 += "█ "
			line3 += "▀ "
		case 'J':
			line1 += "  █ "
			line2 += "▄ █ "
			line3 += "▀▀▀ "
		case 'K':
			line1 += "█ █ "
			line2 += "█▀▄ "
			line3 += "▀ ▀ "
		case 'L':
			line1 += "█   "
			line2 += "█   "
			line3 += "▀▀▀ "
		case 'M':
			line1 += "█▀▄▀█ "
			line2 += "█ ▀ █ "
			line3 += "▀   ▀ "
		case 'N':
			line1 += "█▄ █ "
			line2 += "█ ▀█ "
			line3 += "▀  ▀ "
		case 'O':
			line1 += "█▀█ "
			line2 += "█ █ "
			line3 += "▀▀▀ "
		case 'P':
			line1 += "█▀█ "
			line2 += "█▀▀ "
			line3 += "▀   "
		case 'Q':
			line1 += "█▀█ "
			line2 += "█ █ "
			line3 += "▀▀█ "
		case 'R':
			line1 += "█▀█ "
			line2 += "██▄ "
			line3 += "▀ ▀ "
		case 'S':
			line1 += "█▀▀ "
			line2 += "▀▀█ "
			line3 += "▀▀▀ "
		case 'T':
			line1 += "▀█▀ "
			line2 += " █  "
			line3 += " ▀  "
		case 'U':
			line1 += "█ █ "
			line2 += "█ █ "
			line3 += "▀▀▀ "
		case 'V':
			line1 += "█ █ "
			line2 += "█ █ "
			line3 += " ▀  "
		case 'W':
			line1 += "█ █ █ "
			line2 += "█ █ █ "
			line3 += "▀▀▀▀▀ "
		case 'X':
			line1 += "█ █ "
			line2 += "▄▀▄ "
			line3 += "▀ ▀ "
		case 'Y':
			line1 += "█ █ "
			line2 += "▀█▀ "
			line3 += " ▀  "
		case 'Z':
			line1 += "▀▀█ "
			line2 += "▄▀  "
			line3 += "▀▀▀ "
		default:
			// For any other character, use the character itself
			line1 += string(char) + "  "
			line2 += "   "
			line3 += "   "
		}
	}
	
	return []string{
		strings.TrimRight(line1, " "),
		strings.TrimRight(line2, " "),
		strings.TrimRight(line3, " "),
	}
}

func (m Model) renderStatusLine() string {
	var status string
	var showSpinner bool
	
	switch m.state {
	case StateReady:
		status = "" // Don't show Ready status
		showSpinner = false
	case StateWaitingForResponse:
		if m.currentRunID != "" {
			status = "Thinking... (esc to cancel)"
		} else {
			status = "Thinking..."
		}
		showSpinner = true
	case StateApprovalRequired:
		status = "" // Don't show status - approval dialog is in message list
		showSpinner = false
	case StateConnecting:
		status = "Connecting..."
		showSpinner = true
	default:
		status = ""
		showSpinner = false
	}

	if status == "" {
		return ""
	}

	statusStyle := lipgloss.NewStyle().
		Foreground(Styles.Spinner.GetForeground()).
		Italic(true)

	// Match the alignment of messages (2-char prefix like "> " or "⏺ ")
	if showSpinner {
		// The Dot spinner frames already include a space (e.g., "⣾ ")
		// So we don't need to add another space
		return fmt.Sprintf("%s%s", m.spinner.View(), statusStyle.Render(status))
	}
	// Use two spaces for alignment when no spinner
	return fmt.Sprintf("  %s", statusStyle.Render(status))
}

func (m Model) renderMessages() string {
	var s strings.Builder

	if len(m.messages) == 0 && m.welcomeText != "" {
		s.WriteString(m.welcomeText)
		s.WriteString("\n\n")
	}

	for _, msg := range m.messages {
		// Pass approvalList only for approval_request messages
		var listPtr *list.Model
		if msg.Role == "approval_request" && m.state == StateApprovalRequired {
			listPtr = &m.approvalList
		}
		// Pass terminal width for smart truncation
		rendered := renderMessageWithWidth(msg, m.debugMode, m.pulseFrame, listPtr, m.width)
		s.WriteString(rendered)
	}

	// Add status line at the bottom of messages
	statusLine := m.renderStatusLine()
	if statusLine != "" {
		s.WriteString(statusLine)
		s.WriteString("\n")
	}

	return s.String()
}

func (m Model) renderInput() string {
	var s strings.Builder
	
	// Input field (no padding - border aligns with message indicators)
	s.WriteString(m.input.View())
	s.WriteString("\n")
	
	// Help text (with padding to align with message content)
	helpView := m.getContextualHelp()
	if helpView != "" {
		s.WriteString("\n")
		// Add 2 spaces to align help text with message content
		s.WriteString("  " + helpView)
	}
	
	return s.String()
}

// getContextualHelp returns help text based on current state
func (m Model) getContextualHelp() string {
	// Create a custom keymap with context-specific bindings
	var contextKeys []key.Binding
	
	switch m.state {
	case StateReady:
		contextKeys = []key.Binding{
			key.NewBinding(
				key.WithKeys("enter"),
				key.WithHelp("enter", "send"),
			),
			key.NewBinding(
				key.WithKeys("alt+enter"),
				key.WithHelp("alt+enter", "new line"),
			),
			key.NewBinding(
				key.WithKeys("ctrl+c"),
				key.WithHelp("ctrl+c", "quit"),
			),
		}
	case StateWaitingForResponse:
		contextKeys = []key.Binding{
			key.NewBinding(
				key.WithKeys("esc"),
				key.WithHelp("esc", "cancel"),
			),
			key.NewBinding(
				key.WithKeys("ctrl+c"),
				key.WithHelp("ctrl+c", "quit"),
			),
		}
	case StateApprovalRequired:
		contextKeys = []key.Binding{
			key.NewBinding(
				key.WithKeys("↑/↓"),
				key.WithHelp("↑/↓", "navigate"),
			),
			key.NewBinding(
				key.WithKeys("enter"),
				key.WithHelp("enter", "select"),
			),
			key.NewBinding(
				key.WithKeys("esc"),
				key.WithHelp("esc", "deny"),
			),
		}
	case StateSessionList:
		contextKeys = []key.Binding{
			key.NewBinding(
				key.WithKeys("↑/↓"),
				key.WithHelp("↑/↓", "navigate"),
			),
			key.NewBinding(
				key.WithKeys("←/→"),
				key.WithHelp("←/→", "page"),
			),
			key.NewBinding(
				key.WithKeys("enter"),
				key.WithHelp("enter", "select"),
			),
			key.NewBinding(
				key.WithKeys("tab"),
				key.WithHelp("tab", "new session"),
			),
			key.NewBinding(
				key.WithKeys("ctrl+c"),
				key.WithHelp("ctrl+c", "quit"),
			),
		}
	default:
		contextKeys = []key.Binding{
			key.NewBinding(
				key.WithKeys("ctrl+c"),
				key.WithHelp("ctrl+c", "quit"),
			),
		}
	}
	
	// Create a simple help line from the context keys
	helpStrs := make([]string, len(contextKeys))
	for i, k := range contextKeys {
		helpStrs[i] = k.Help().Key + " " + k.Help().Desc
	}
	
	helpStyle := Styles.Help
	return helpStyle.Render(strings.Join(helpStrs, " • "))
}


func (m Model) renderError() string {
	errorStyle := lipgloss.NewStyle().
		Foreground(Styles.Error.GetForeground()).
		Bold(true)

	helpStyle := lipgloss.NewStyle().
		Foreground(Styles.Debug.GetForeground())

	var output strings.Builder
	output.WriteString(errorStyle.Render(fmt.Sprintf("Error: %v", m.err)))
	output.WriteString("\n\n")
	output.WriteString(helpStyle.Render("This usually means the server failed to start or is not accessible."))
	output.WriteString("\n")
	output.WriteString(helpStyle.Render("Press Ctrl+C to exit."))

	return output.String()
}

func (m Model) renderConnecting() string {
	connectStyle := lipgloss.NewStyle().
		Foreground(Styles.Info.GetForeground())

	return connectStyle.Render("Connecting to server...")
}

func (m Model) renderSessionList() string {
	var s strings.Builder

	headerStyle := lipgloss.NewStyle().
		Foreground(Styles.SessionTitle.GetForeground()).
		Bold(true)

	s.WriteString(headerStyle.Render("Select a session or press Tab to create new:"))
	s.WriteString("\n\n")

	// Get the slice bounds for the current page
	start, end := m.paginator.GetSliceBounds(len(m.availableSessions))
	visibleSessions := m.availableSessions[start:end]

	// Render visible sessions on current page
	for i, session := range visibleSessions {
		cursor := "  "
		if i == m.selectedSession {
			cursor = "> "
		}

		sessionInfo := fmt.Sprintf("%s%s - %d messages",
			cursor,
			session.ID[:8],
			session.MessageCount)

		if session.Archived {
			sessionInfo += " (archived)"
		}

		s.WriteString(sessionInfo)
		s.WriteString("\n")
	}

	// Add some spacing
	s.WriteString("\n")
	
	// Show pagination dots if there are multiple pages
	if m.paginator.TotalPages > 1 {
		s.WriteString(fmt.Sprintf("  Page %d of %d  ", m.paginator.Page+1, m.paginator.TotalPages))
		s.WriteString(m.paginator.View())
		s.WriteString("\n\n")
	}
	
	// Instructions
	infoStyle := Styles.Help
	s.WriteString(infoStyle.Render("↑↓ navigate items • ←→ change page • Enter select • Tab new session"))

	return s.String()
}

// Helper methods for actions

func (m Model) sendMessage() (Model, tea.Cmd) {
	text := strings.TrimSpace(m.input.Value())
	if text == "" {
		return m, nil
	}

	// Add user message
	m.messages = append(m.messages, Message{
		Role:    "user",
		Content: text,
	})

	// Clear input
	m.input.SetValue("")

	// If this is the first message, recalculate viewport height since header will disappear
	if len(m.messages) == 1 {
		footerLines := m.input.Height() + 3  // Textarea height + help + spacing
		// No header anymore, but still 2 lines of top spacing
		m.viewport.Height = m.height - 2 - footerLines
	}

	// Update state
	m.state = StateWaitingForResponse
	// Update viewport to show new message and status
	m.updateViewport()

	// Start run
	return m, m.startRun(text)
}

func (m Model) startRun(prompt string) tea.Cmd {
	return func() tea.Msg {
		var sessionPtr *string
		if m.sessionID != "" {
			sessionPtr = &m.sessionID
		}

		resp, err := m.rpcClient.StartRun(prompt, sessionPtr, nil)
		if err != nil {
			return ErrorMsg{Error: err}
		}

		// Return the run info so the Update method can save it
		return RunStartedMsg{
			RunID:     resp.RunID,
			SessionID: resp.SessionID,
		}
	}
}

func (m Model) handleApprovalListChoice() (Model, tea.Cmd) {
	// Get selected item from list
	selectedItem, ok := m.approvalList.SelectedItem().(ApprovalOption)
	if !ok {
		return m, nil
	}

	var approved bool
	var alwaysApprove bool

	switch selectedItem.Value {
	case "yes":
		approved = true
		alwaysApprove = false
	case "always":
		approved = true
		alwaysApprove = true
	case "no":
		approved = false
		alwaysApprove = false
	default:
		return m, nil
	}

	// Save approval ID before clearing
	approvalID := m.pendingTool.ApprovalID
	
	// Remove approval message from messages
	for i := len(m.messages) - 1; i >= 0; i-- {
		if m.messages[i].Role == "approval_request" {
			m.messages = append(m.messages[:i], m.messages[i+1:]...)
			break
		}
	}
	
	// Change state FIRST before clearing approval data
	m.state = StateWaitingForResponse
	m.pendingTool = nil
	m.approvalList = list.Model{}  // Clear the list
	
	// Recalculate viewport height for new footer size
	headerLines := 0
	if len(m.messages) == 0 {
		headerLines = 5
		if m.sessionID != "" {
			headerLines = 7
		}
	}
	footerLines := m.input.Height() + 3  // Textarea height + help + spacing
	topSpacing := 2  // Always 2 lines of top spacing
	m.viewport.Height = m.height - headerLines - footerLines - topSpacing
	m.updateViewport()

	// Send approval response AND clear screen to force full redraw
	return m, tea.Batch(
		tea.ClearScreen,
		func() tea.Msg {
			err := m.rpcClient.ToolApprovalResponse(approvalID, approved, alwaysApprove)
			if err != nil {
				return ErrorMsg{Error: err}
			}
			return nil
		},
	)
}

func (m Model) listSessions() (Model, tea.Cmd) {
	return m, func() tea.Msg {
		sessions, err := m.rpcClient.ListSessions()
		if err != nil {
			// No sessions or error, just start with new session
			return SessionsListedMsg{Sessions: nil, Error: err}
		}

		return SessionsListedMsg{Sessions: sessions, Error: nil}
	}
}

func (m Model) selectSession() (Model, tea.Cmd) {
	// Get the actual session index from the current page
	start, _ := m.paginator.GetSliceBounds(len(m.availableSessions))
	actualIndex := start + m.selectedSession
	
	if actualIndex < len(m.availableSessions) {
		m.sessionID = m.availableSessions[actualIndex].ID
		m.state = StateReady
		m.updateViewport()
		// TODO: Load session history
	}
	return m, nil
}

func (m *Model) updateViewport() {
	m.viewport.SetContent(m.renderMessages())
	m.viewport.GotoBottom()
}

// updateViewportContent updates the viewport content without changing scroll position
func (m *Model) updateViewportContent() {
	// Save current scroll position
	oldYOffset := m.viewport.YOffset
	oldYPosition := m.viewport.YPosition
	
	// Update content
	m.viewport.SetContent(m.renderMessages())
	
	// Restore scroll position
	m.viewport.YOffset = oldYOffset
	m.viewport.YPosition = oldYPosition
}