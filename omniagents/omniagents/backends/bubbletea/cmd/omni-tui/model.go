package main

import (
	"fmt"
	"os"
	"strconv"
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
	selectedSession   int // Index within current page (0 to perPage-1)
	selectedIndex     int // Absolute index across all sessions

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

	// Double Ctrl+C tracking
	firstCtrlCTime *time.Time // Track when first Ctrl+C was pressed

    // Usage tracking (updated via token events and run_end)
    usageTotals *UsageDetails
    // Current model info and last-turn delta input tokens
    modelName         string
    contextWindow     int
    lastDeltaInputTok int
    // Totals from last turn/run for stable context remaining calc
    lastTotalsInputTok int
    maxOutputTokens    int
}

// Message represents a chat message
type Message struct {
	Role     string // "user", "assistant", "tool_call", "tool_result", "approval_request"
	Content  string
	ToolCall *ToolCallInfo
	Approval *ToolApprovalInfo // For approval_request messages
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
	ta.CharLimit = 0 // No limit on characters
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
		return "   " // Same width as prompt for alignment
	})

	// Set styles for focused state
	ta.FocusedStyle.Base = lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(Styles.InputBorder.GetForeground())
	ta.FocusedStyle.CursorLine = lipgloss.NewStyle() // No background for cursor line
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

// ClearCtrlCMsg is sent to clear the first Ctrl+C state after timeout
type ClearCtrlCMsg struct{}

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

		// Set up callback for when RPC receiveLoop is ready
		m.rpcClient.SetOnReady(func() {
			if m.debugMode {
				fmt.Fprintf(os.Stderr, "[DEBUG] RPC receiveLoop is ready\n")
			}
			// Send a message indicating RPC is ready
			send(RPCReadyMsg{})
		})

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
			// Double Ctrl+C to quit
			now := time.Now()
			if m.firstCtrlCTime != nil {
				// Check if second Ctrl+C is within 2 seconds
				if now.Sub(*m.firstCtrlCTime) < 2*time.Second {
					// Second Ctrl+C within timeout - quit
					return m, tea.Quit
				}
			}
			// First Ctrl+C or timeout expired - record time
			m.firstCtrlCTime = &now

			// Clear the first Ctrl+C after 2 seconds
			return m, tea.Tick(2*time.Second, func(t time.Time) tea.Msg {
				return ClearCtrlCMsg{}
			})

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
				m.approvalList = list.Model{} // Clear the list

				// Recalculate viewport height for new footer size
				headerLines := 0
				if len(m.messages) == 0 {
					headerLines = 5
					if m.sessionID != "" {
						headerLines = 7
					}
				}
				footerLines := m.input.Height() + 3 // Textarea height + help + spacing
				topSpacing := 2                     // Always 2 lines of top spacing
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
				m.currentRunID = "" // Clear immediately to prevent double cancellation

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
		m.viewport.Width = msg.Width - 4 // Account for padding

		// Calculate viewport height based on current state
		headerLines := 0
		if len(m.messages) == 0 {
			headerLines = 5 // Header with ASCII art + newlines
			if m.sessionID != "" {
				headerLines = 7 // Add lines for session display
			}
		}
		// Calculate footer lines based on textarea height
		footerLines := m.input.Height() + 3 // Textarea height + help (2 lines) + spacing (1 line)
		if m.state == StateApprovalRequired {
			footerLines = 3 // Just help text when approval is in the message list
		}
		topSpacing := 2 // Always 2 lines of top spacing
		m.viewport.Height = msg.Height - headerLines - footerLines - topSpacing

		// Update textarea width
		// Pass full width; textarea.SetWidth accounts for border frame size
		m.input.SetWidth(msg.Width)

		// Update help width
		m.help.Width = msg.Width - 4

		// If showing approval dialog, resize the approval list to match inner box width
		if m.state == StateApprovalRequired {
			listWidth := m.viewport.Width - 6 // borders (2) + padding (4)
			if listWidth < 30 {
				listWidth = 30
			}
			m.approvalList.SetSize(listWidth, m.approvalList.Height())
		}

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
			// Wait for RPC to be ready before listing sessions
			// Keep showing "Connecting..." until RPC is ready
			m.state = StateConnecting
			if m.debugMode {
				fmt.Fprintf(os.Stderr, "[DEBUG] Resume mode: waiting for RPC to be ready\n")
			}
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
		// Create approval list sized to the inner width of the approval box
		listWidth := m.viewport.Width - 6 // borders (2) + padding (4)
		if listWidth < 30 {
			listWidth = 30
		}
		m.approvalList = CreateApprovalList(msg.Tool, msg.Arguments, listWidth)
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
		// Reset usage totals at the start of a run
		m.usageTotals = nil

	case RunEndMsg:
		// Only change state to Ready if not waiting for approval
		if m.state != StateApprovalRequired {
			// If the run ended with an error and error details are present, surface it
			if strings.ToLower(msg.EndReason) == "error" && msg.Error != nil {
				m.state = StateError
				m.err = fmt.Errorf(msg.Error.Message)
			} else {
				m.state = StateReady
			}
		}
		m.currentRunID = ""
		// Capture final usage totals if provided
		if msg.Usage != nil {
			totals := *msg.Usage
			m.usageTotals = &totals
		}
		// Capture final model info if provided
		if msg.Model != "" {
			m.modelName = msg.Model
		}
		if msg.ContextWindow > 0 {
			m.contextWindow = msg.ContextWindow
		}
		// Don't clear pendingTool or approvalList - let user handle approval
		m.updateViewport()

	case TokenUsageMsg:
		// Update running totals so footer can reflect near-real-time usage
		totals := msg.Totals
		m.usageTotals = &totals
		// Update model and context window if provided
		if msg.Model != "" {
			m.modelName = msg.Model
		}
		if msg.ContextWindow > 0 {
			m.contextWindow = msg.ContextWindow
		}
		// Track last turn's input tokens for context usage estimation
		m.lastDeltaInputTok = msg.Delta.InputTokens

	case RPCReadyMsg:
		if m.debugMode {
			fmt.Fprintf(os.Stderr, "[DEBUG] Received RPCReadyMsg\n")
		}
		// If we're in resume mode and still connecting, now we can list sessions
		if m.resumeMode && m.state == StateConnecting {
			if m.debugMode {
				fmt.Fprintf(os.Stderr, "[DEBUG] RPC ready, listing sessions\n")
				fmt.Fprintf(os.Stderr, "[DEBUG] rpcClient is nil: %v\n", m.rpcClient == nil)
			}
			_, cmd := m.listSessions()
			if m.debugMode {
				fmt.Fprintf(os.Stderr, "[DEBUG] listSessions returned cmd is nil: %v\n", cmd == nil)
			}
			// Add to cmds to be batched at the end with listenForMessages
			cmds = append(cmds, cmd)
		}

	case SessionsListedMsg:
		if m.debugMode {
			fmt.Fprintf(os.Stderr, "[DEBUG] Received SessionsListedMsg: error=%v, sessions=%d\n",
				msg.Error, len(msg.Sessions))
		}
		if msg.Error != nil || msg.Sessions == nil || len(msg.Sessions) == 0 {
			// No sessions or error, start with new session
			if m.debugMode {
				fmt.Fprintf(os.Stderr, "[DEBUG] No sessions or error, starting with new session\n")
			}
			m.state = StateReady
			m.updateViewport()
		} else {
			// Show session list
			if m.debugMode {
				fmt.Fprintf(os.Stderr, "[DEBUG] Showing session list with %d sessions\n", len(msg.Sessions))
			}
			m.availableSessions = msg.Sessions
			m.selectedSession = 0
			m.selectedIndex = 0
			// Setup paginator
			m.paginator.SetTotalPages(len(msg.Sessions))
			m.paginator.Page = 0
			m.state = StateSessionList
		}

	case ClearCtrlCMsg:
		// Clear the first Ctrl+C state after timeout
		m.firstCtrlCTime = nil
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
			m.updateViewportContent() // Use content-only update to preserve scroll
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
			m.pulseFrame = (m.pulseFrame + 1) % 8 // 8 frames for smooth pulse
			m.updateViewportContent()             // Use content-only update to preserve scroll
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
		// Count visible (wrapped) lines so the textarea grows when a single long line wraps
		lineCount := m.input.VisibleLineCount()

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
			footerLines := m.input.Height() + 3 // Textarea height + help + spacing
			topSpacing := 2                     // Always 2 lines of top spacing
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
	topSpacing := 2 // Always 2 lines of top spacing
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
			Width(m.viewport.Width)
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
	content.WriteString(footer) // No padding for footer - border aligns with messages

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
		// Pass viewport content width for correct wrapping inside the messages area
		rendered := renderMessageWithWidth(msg, m.debugMode, m.pulseFrame, listPtr, m.viewport.Width)
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

	// Footer line: help on the left, usage (context remaining) on the right
	usage, pct, hasPct := m.getUsageFooterText()
	helpView := m.getContextualHelp()
	if usage != "" || helpView != "" {
		// We want the help text aligned with the message prompt (which sits
		// after two leading spaces in the middle section). So add two leading
		// spaces here. Do not add trailing spaces so the token text ends at
		// the last column (as desired).
		s.WriteString("  ") // left outer padding to align with messages

		// Available content width after the 2-space left padding and a symmetric
		// 2-column right margin so the token info is indented from the right edge
		// the same amount the help is indented from the left.
		contentWidth := m.width - 4
		left := helpView // help already styled
		// Choose style based on remaining context percentage
		rightStyle := Styles.Help
		if hasPct {
			if pct <= 10 {
				rightStyle = Styles.Error
			} else if pct <= 25 {
				rightStyle = Styles.Warning
			}
		}
		right := rightStyle.Render(usage)
		// Compute visual widths (ignore ANSI sequences)
		leftW := lipgloss.Width(left)
		rightW := lipgloss.Width(right)

		// Space between left text and right text
		gap := contentWidth - leftW - rightW
		if gap < 1 {
			gap = 1
		}

		// Left content
		s.WriteString(left)
		// Gap between left and right content
		s.WriteString(strings.Repeat(" ", gap))
		// Right content
		s.WriteString(right)
		// No trailing padding; the 2-column right margin is implicit by reducing
		// contentWidth above so the token ends two columns before the edge.
		s.WriteString("\n")
	}

	return s.String()
}

// getUsageFooterText formats the usage summary for the footer
func (m Model) getUsageFooterText() (string, int, bool) {
    // Use totals.total_tokens from the last completed turn. That entire
    // amount becomes the next run's input size (prior input + prior output).
    if m.modelName != "" && m.contextWindow > 0 && m.usageTotals != nil {
        used := m.usageTotals.TotalTokens
        remaining := m.contextWindow - used
        if remaining < 0 {
            remaining = 0
        }
        pct := (remaining * 100) / m.contextWindow
        if pct < 0 {
            pct = 0
        } else if pct > 100 {
            pct = 100
        }
        return fmt.Sprintf("context remaining: %d%% • %s", pct, m.modelName), pct, true
    }
    return "", 0, false
}

// formatWithCommas formats an integer with thousands separators (e.g., 12345 -> "12,345")
func formatWithCommas(n int) string {
	// Handle negative numbers
	neg := n < 0
	if neg {
		n = -n
	}

	s := strconv.Itoa(n)
	l := len(s)
	if l <= 3 {
		if neg {
			return "-" + s
		}
		return s
	}

	// Determine length of the first group
	first := l % 3
	if first == 0 {
		first = 3
	}

	var b strings.Builder
	if neg {
		b.WriteByte('-')
	}
	b.WriteString(s[:first])
	for i := first; i < l; i += 3 {
		b.WriteByte(',')
		b.WriteString(s[i : i+3])
	}
	return b.String()
}

// getContextualHelp returns help text based on current state
func (m Model) getContextualHelp() string {
	// Create a custom keymap with context-specific bindings
	var contextKeys []key.Binding

	// Check if we're waiting for second Ctrl+C
	if m.firstCtrlCTime != nil && time.Since(*m.firstCtrlCTime) < 2*time.Second {
		// Show special help text when waiting for second Ctrl+C
		helpStyle := Styles.Help
		warnStyle := lipgloss.NewStyle().
			Foreground(lipgloss.Color("#ff9900")).
			Bold(true)
		return warnStyle.Render("Press Ctrl+C again to quit") + " • " +
			helpStyle.Render("or wait to continue")
	}

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
    output.WriteString(helpStyle.Render("The run ended with an error."))
    output.WriteString("\n")
    output.WriteString(helpStyle.Render("Common causes: invalid API key, insufficient quota, rate limiting, model not available, or network issues."))
    output.WriteString("\n")
    output.WriteString(helpStyle.Render("Press Ctrl+C to exit, or start a new run."))

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

		// Build session line directly
		sessionLine := cursor + session.ID[:8]

		// Add timestamp if available
		if session.CreatedAt != "" {
			timestamp := session.CreatedAt
			if len(timestamp) > 10 {
				timestamp = timestamp[:10]
			}
			sessionLine += " • " + timestamp
		}

		// Add message count
		sessionLine += fmt.Sprintf(" • %d messages", session.MessageCount)

		// Add archived tag if needed
		if session.Archived {
			sessionLine += " [archived]"
		}

		// Always add last message preview if available
		if session.LastMessage != nil {
			content := session.LastMessage.GetContentText()
			// Debug: print what we got
			if m.debugMode {
				fmt.Fprintf(os.Stderr, "[DEBUG] LastMessage content type: %T, value: %v, extracted: %s\n",
					session.LastMessage.Content, session.LastMessage.Content, content)
			}
			if content != "" {
				// Use the truncateMessageForList function which handles newlines
				content = m.truncateMessageForList(content, 50)
				sessionLine += " • " + content
			}
		} else if session.FirstMessage != nil {
			content := session.FirstMessage.GetContentText()
			if m.debugMode {
				fmt.Fprintf(os.Stderr, "[DEBUG] FirstMessage content type: %T, value: %v, extracted: %s\n",
					session.FirstMessage.Content, session.FirstMessage.Content, content)
			}
			if content != "" {
				// Use the truncateMessageForList function which handles newlines
				content = m.truncateMessageForList(content, 50)
				sessionLine += " • " + content
			}
		}

		// Style the line
		lineStyle := lipgloss.NewStyle()
		if i == m.selectedSession {
			lineStyle = lineStyle.Bold(true).
				Foreground(Styles.SessionTitle.GetForeground())
		} else {
			lineStyle = lineStyle.
				Foreground(Styles.Help.GetForeground())
		}

		s.WriteString(lineStyle.Render(sessionLine))
		s.WriteString("\n")
	}

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
		footerLines := m.input.Height() + 3 // Textarea height + help + spacing
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
	m.approvalList = list.Model{} // Clear the list

	// Recalculate viewport height for new footer size
	headerLines := 0
	if len(m.messages) == 0 {
		headerLines = 5
		if m.sessionID != "" {
			headerLines = 7
		}
	}
	footerLines := m.input.Height() + 3 // Textarea height + help + spacing
	topSpacing := 2                     // Always 2 lines of top spacing
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
	if m.debugMode {
		fmt.Fprintln(os.Stderr, "[DEBUG] listSessions: starting async fetch")
	}
	client := m.rpcClient
	debug := m.debugMode
	msgChan := m.msgChan
	go func() {
		if debug {
			fmt.Fprintln(os.Stderr, "[DEBUG] listSessions goroutine: executing")
		}
		sessions, err := client.ListSessions()
		if err != nil {
			if debug {
				fmt.Fprintln(os.Stderr, "[DEBUG] listSessions goroutine: error:", err)
			}
			if msgChan != nil {
				msgChan <- SessionsListedMsg{Sessions: nil, Error: err}
			}
			return
		}
		if debug {
			fmt.Fprintln(os.Stderr, "[DEBUG] listSessions goroutine: got", len(sessions), "sessions")
		}
		if msgChan != nil {
			msgChan <- SessionsListedMsg{Sessions: sessions, Error: nil}
		}
	}()
	return m, nil
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

// truncateMessageForList truncates a message for display in the session list
func (m Model) truncateMessageForList(content string, maxWidth int) string {
	// Trim leading/trailing whitespace
	content = strings.TrimSpace(content)

	// Intelligently handle newlines
	lines := strings.Split(content, "\n")

	// If multi-line, try to use first non-empty line
	// This helps with messages that start with empty lines or have structure
	firstMeaningfulLine := ""
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line != "" {
			firstMeaningfulLine = line
			break
		}
	}

	// If we found a meaningful first line, use it
	// Otherwise fall back to joining all lines
	if firstMeaningfulLine != "" {
		content = firstMeaningfulLine
		// Add indicator if there were multiple lines
		if len(lines) > 1 {
			// Check if other lines have content
			hasMoreContent := false
			for i := 1; i < len(lines); i++ {
				if strings.TrimSpace(lines[i]) != "" {
					hasMoreContent = true
					break
				}
			}
			if hasMoreContent {
				// Add ellipsis to indicate there's more content
				if len(content) <= maxWidth-4 {
					content = content + " ..."
				}
			}
		}
	} else {
		// No meaningful content found, join all lines
		content = strings.Join(lines, " ")
	}

	// Clean up excessive whitespace
	content = strings.TrimSpace(content)

	// Collapse multiple spaces into single space
	for strings.Contains(content, "  ") {
		content = strings.ReplaceAll(content, "  ", " ")
	}

	// Final truncation if necessary
	if len(content) > maxWidth {
		if maxWidth > 3 {
			content = content[:maxWidth-3] + "..."
		} else {
			content = "..."
		}
	}

	return content
}
