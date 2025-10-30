package main

import (
	"flag"
	"fmt"
	"log"
	"os"

	tea "github.com/charmbracelet/bubbletea"
)

func main() {
	var (
		serverURL   string
		token       string
		agentName   string
		welcomeText string
		safeMode    bool
		sessionID   string
		debug       bool
		standalone  bool
		resume      bool
		themeName   string
		saveTheme   bool
		initialMsg  string
	)

	// Parse command line flags
	flag.StringVar(&serverURL, "server", "", "WebSocket server URL (required)")
	flag.StringVar(&token, "token", "", "Authentication token")
	flag.StringVar(&agentName, "name", "OmniAgent", "Agent name")
	flag.StringVar(&welcomeText, "welcome", "Welcome! I'm here to help. What can I do for you today?", "Welcome message")
	flag.BoolVar(&safeMode, "safe-mode", false, "Enable safe mode (require tool approval)")
	flag.StringVar(&sessionID, "session", "", "Resume specific session ID")
	flag.BoolVar(&resume, "resume", false, "Show session list to resume a previous session")
	flag.BoolVar(&debug, "debug", false, "Enable debug mode")
	flag.BoolVar(&standalone, "standalone", false, "Run in standalone mode (connect to external server)")
	flag.StringVar(&themeName, "theme", "", "Color theme to use (e.g., tokyonight)")
	flag.BoolVar(&saveTheme, "save-theme", false, "Save the theme choice to config")
	flag.StringVar(&initialMsg, "initial-message", "", "Message to send immediately after connecting")
	flag.Parse()

	// Validate required parameters
	if serverURL == "" {
		fmt.Fprintf(os.Stderr, "Error: --server is required\n")
		fmt.Fprintf(os.Stderr, "\nUsage: %s --server <url> [options]\n", os.Args[0])
		fmt.Fprintf(os.Stderr, "\nOptions:\n")
		flag.PrintDefaults()
		fmt.Fprintf(os.Stderr, "\nExamples:\n")
		fmt.Fprintf(os.Stderr, "  Standalone mode (connect to external server):\n")
		fmt.Fprintf(os.Stderr, "    %s --standalone --server ws://localhost:8000/ws\n", os.Args[0])
		fmt.Fprintf(os.Stderr, "\n  With authentication:\n")
		fmt.Fprintf(os.Stderr, "    %s --standalone --server ws://localhost:8000/ws --token mytoken\n", os.Args[0])
		os.Exit(1)
	}

	// If running in standalone mode, show connection info
	if standalone {
		fmt.Fprintf(os.Stderr, "Running in standalone mode\n")
		fmt.Fprintf(os.Stderr, "Connecting to: %s\n", serverURL)
		if token != "" {
			fmt.Fprintf(os.Stderr, "Using authentication token\n")
		}
		fmt.Fprintf(os.Stderr, "\n")
	}

	// Set debug environment variable if flag is set
	if debug {
		os.Setenv("OMNI_DEBUG", "1")
	}

	// Load theme
	if themeName == "" {
		// No theme specified on command line, use preference
		themeName = GetThemePreference()
	}

	if err := LoadTheme(themeName); err != nil {
		// Fall back to default theme if specified theme not found
		if debug {
			fmt.Fprintf(os.Stderr, "[DEBUG] Theme '%s' not found, using default\n", themeName)
		}
		LoadDefaultTheme()
	}

	// Save theme preference if requested
	if saveTheme && themeName != "" {
		if err := SaveThemePreference(themeName); err != nil {
			if debug {
				fmt.Fprintf(os.Stderr, "[DEBUG] Failed to save theme preference: %v\n", err)
			}
		}
	}

	// Initialize styles from theme
	initStyles()
	initApprovalStyles()

	// Create RPC client
	client := NewRPCClient(serverURL, token)

	// Initialize model
	model := NewModel(client, agentName, welcomeText, safeMode, sessionID, resume, initialMsg)

	// Create program with options
	p := tea.NewProgram(
		model,
		tea.WithAltScreen(),       // Use alternate screen buffer
		tea.WithMouseCellMotion(), // Mouse support with cell-level motion (less frequent events)
	)

	// Run the program
	if _, err := p.Run(); err != nil {
		log.Fatal(err)
	}

	// Clean up
	if client != nil {
		client.Close()
	}
}
