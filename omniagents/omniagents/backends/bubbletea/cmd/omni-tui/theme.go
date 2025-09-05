package main

import (
	"embed"
	"fmt"
	"os"
	"path/filepath"

	"github.com/charmbracelet/lipgloss"
	"gopkg.in/yaml.v3"
)

// Theme represents a color theme for the TUI
type Theme struct {
	Name   string      `yaml:"name"`
	Colors ThemeColors `yaml:"colors"`
}

// ThemeColors defines all the colors used in the theme
type ThemeColors struct {
	Primary   PrimaryColors   `yaml:"primary"`
	Semantic  SemanticColors  `yaml:"semantic"`
	UI        UIColors        `yaml:"ui"`
}

// PrimaryColors are the base colors
type PrimaryColors struct {
	Background string `yaml:"background"`
	Foreground string `yaml:"foreground"`
}

// SemanticColors are colors for specific content types
type SemanticColors struct {
	UserMessage      string `yaml:"user_message"`
	AssistantMessage string `yaml:"assistant_message"`
	ToolName         string `yaml:"tool_name"`
	ToolResult       string `yaml:"tool_result"`
	Error            string `yaml:"error"`
	Warning          string `yaml:"warning"`
	Info             string `yaml:"info"`
	Debug            string `yaml:"debug"`
}

// UIColors are colors for UI elements
type UIColors struct {
	Border    string `yaml:"border"`
	Selection string `yaml:"selection"`
	Cursor    string `yaml:"cursor"`
	Header    string `yaml:"header"`
	Help      string `yaml:"help"`
	
	// Additional UI elements
	InputBorder      string `yaml:"input_border"`
	InputText        string `yaml:"input_text"`
	InputPrompt      string `yaml:"input_prompt"`
	MessagePrompt    string `yaml:"message_prompt"`
	Spinner          string `yaml:"spinner"`
	SessionTitle     string `yaml:"session_title"`
	SessionSubtitle  string `yaml:"session_subtitle"`
	ApprovalTitle    string `yaml:"approval_title"`
	ApprovalOption   string `yaml:"approval_option"`
	ApprovalSelected string `yaml:"approval_selected"`
}

// Embed built-in themes
//go:embed themes/*.yaml
var builtinThemes embed.FS

// CurrentTheme is the active theme
var CurrentTheme *Theme

// ThemeStyles holds the compiled lipgloss styles
type ThemeStyles struct {
	// Semantic styles
	UserMessage      lipgloss.Style
	AssistantMessage lipgloss.Style
	ToolName         lipgloss.Style
	ToolResult       lipgloss.Style
	Error            lipgloss.Style
	Warning          lipgloss.Style
	Info             lipgloss.Style
	Debug            lipgloss.Style
	
	// UI styles
	Border           lipgloss.Style
	Selection        lipgloss.Style
	Header           lipgloss.Style
	Help             lipgloss.Style
	InputBorder      lipgloss.Style
	InputText        lipgloss.Style
	InputPrompt      lipgloss.Style
	MessagePrompt    lipgloss.Style
	Spinner          lipgloss.Style
	SessionTitle     lipgloss.Style
	SessionSubtitle  lipgloss.Style
	ApprovalTitle    lipgloss.Style
	ApprovalOption   lipgloss.Style
	ApprovalSelected lipgloss.Style
}

// Styles is the global compiled theme styles
var Styles ThemeStyles

// LoadTheme loads a theme by name
func LoadTheme(name string) error {
	// First, try to load from user's config directory
	homeDir, err := os.UserHomeDir()
	if err == nil {
		userThemePath := filepath.Join(homeDir, ".config", "omniagents", "themes", name+".yaml")
		if data, err := os.ReadFile(userThemePath); err == nil {
			var theme Theme
			if err := yaml.Unmarshal(data, &theme); err == nil {
				CurrentTheme = &theme
				compileStyles()
				return nil
			}
		}
	}

	// Try built-in themes
	builtinPath := "themes/" + name + ".yaml"
	data, err := builtinThemes.ReadFile(builtinPath)
	if err != nil {
		return fmt.Errorf("failed to read built-in theme %s: %w", name, err)
	}
	
	var theme Theme
	if err := yaml.Unmarshal(data, &theme); err != nil {
		return fmt.Errorf("failed to parse theme %s: %w", name, err)
	}
	
	CurrentTheme = &theme
	compileStyles()
	return nil
}

// compileStyles creates lipgloss styles from the current theme
func compileStyles() {
	if CurrentTheme == nil {
		// Use default theme if none loaded
		LoadDefaultTheme()
		return
	}

	c := CurrentTheme.Colors

	// Semantic styles
	Styles.UserMessage = lipgloss.NewStyle().
		Foreground(lipgloss.Color(c.Semantic.UserMessage))

	Styles.AssistantMessage = lipgloss.NewStyle().
		Foreground(lipgloss.Color(c.Semantic.AssistantMessage))
	
	// Debug: print what color we're using
	if os.Getenv("OMNI_DEBUG") == "1" {
		fmt.Fprintf(os.Stderr, "[DEBUG] Theme colors loaded - Assistant: %s, User: %s\n", 
			c.Semantic.AssistantMessage, c.Semantic.UserMessage)
	}

	Styles.ToolName = lipgloss.NewStyle().
		Foreground(lipgloss.Color(c.Semantic.ToolName)).
		Bold(true)

	Styles.ToolResult = lipgloss.NewStyle().
		Foreground(lipgloss.Color(c.Semantic.ToolResult))

	Styles.Error = lipgloss.NewStyle().
		Foreground(lipgloss.Color(c.Semantic.Error)).
		Bold(true)

	Styles.Warning = lipgloss.NewStyle().
		Foreground(lipgloss.Color(c.Semantic.Warning))

	Styles.Info = lipgloss.NewStyle().
		Foreground(lipgloss.Color(c.Semantic.Info))

	Styles.Debug = lipgloss.NewStyle().
		Foreground(lipgloss.Color(c.Semantic.Debug)).
		Italic(true)

	// UI styles
	Styles.Border = lipgloss.NewStyle().
		Foreground(lipgloss.Color(c.UI.Border))

	Styles.Selection = lipgloss.NewStyle().
		Background(lipgloss.Color(c.UI.Selection))

	Styles.Header = lipgloss.NewStyle().
		Foreground(lipgloss.Color(c.UI.Header)).
		Bold(true)

	Styles.Help = lipgloss.NewStyle().
		Foreground(lipgloss.Color(c.UI.Help))
	
	// Additional UI styles with fallbacks
	if c.UI.InputBorder != "" {
		Styles.InputBorder = lipgloss.NewStyle().
			Foreground(lipgloss.Color(c.UI.InputBorder))
	} else {
		Styles.InputBorder = Styles.Border
	}
	
	if c.UI.InputText != "" {
		Styles.InputText = lipgloss.NewStyle().
			Foreground(lipgloss.Color(c.UI.InputText))
	} else {
		Styles.InputText = lipgloss.NewStyle().
			Foreground(lipgloss.Color(c.Primary.Foreground))
	}
	
	if c.UI.InputPrompt != "" {
		Styles.InputPrompt = lipgloss.NewStyle().
			Foreground(lipgloss.Color(c.UI.InputPrompt))
	} else {
		Styles.InputPrompt = Styles.Info  // Fallback to info color
	}
	
	if c.UI.MessagePrompt != "" {
		Styles.MessagePrompt = lipgloss.NewStyle().
			Foreground(lipgloss.Color(c.UI.MessagePrompt))
	} else {
		Styles.MessagePrompt = Styles.Info  // Fallback to info color
	}
	
	if c.UI.Spinner != "" {
		Styles.Spinner = lipgloss.NewStyle().
			Foreground(lipgloss.Color(c.UI.Spinner))
	} else {
		Styles.Spinner = Styles.Info
	}
	
	if c.UI.SessionTitle != "" {
		Styles.SessionTitle = lipgloss.NewStyle().
			Foreground(lipgloss.Color(c.UI.SessionTitle)).
			Bold(true)
	} else {
		Styles.SessionTitle = Styles.Header
	}
	
	if c.UI.SessionSubtitle != "" {
		Styles.SessionSubtitle = lipgloss.NewStyle().
			Foreground(lipgloss.Color(c.UI.SessionSubtitle))
	} else {
		Styles.SessionSubtitle = Styles.Help
	}
	
	if c.UI.ApprovalTitle != "" {
		Styles.ApprovalTitle = lipgloss.NewStyle().
			Foreground(lipgloss.Color(c.UI.ApprovalTitle)).
			Bold(true)
	} else {
		Styles.ApprovalTitle = Styles.Warning
	}
	
	if c.UI.ApprovalOption != "" {
		Styles.ApprovalOption = lipgloss.NewStyle().
			Foreground(lipgloss.Color(c.UI.ApprovalOption))
	} else {
		Styles.ApprovalOption = Styles.Help
	}
	
	if c.UI.ApprovalSelected != "" {
		Styles.ApprovalSelected = lipgloss.NewStyle().
			Foreground(lipgloss.Color(c.UI.ApprovalSelected)).
			Bold(true)
	} else {
		Styles.ApprovalSelected = Styles.Info.Copy().Bold(true)
	}
}

// LoadDefaultTheme loads the default theme (current colors)
func LoadDefaultTheme() {
	CurrentTheme = &Theme{
		Name: "default",
		Colors: ThemeColors{
			Primary: PrimaryColors{
				Background: "",
				Foreground: "#FFFFFF",
			},
			Semantic: SemanticColors{
				UserMessage:      "10",  // Green
				AssistantMessage: "15",  // White
				ToolName:         "14",  // Cyan
				ToolResult:       "252", // Light gray
				Error:            "196", // Red
				Warning:          "226", // Yellow
				Info:             "14",  // Cyan
				Debug:            "241", // Gray
			},
			UI: UIColors{
				Border:    "241",
				Selection: "14",
				Cursor:    "15",
				Header:    "205", // Magenta
				Help:      "245",
			},
		},
	}
	compileStyles()
}

// GetThemePreference gets the user's theme preference from config
func GetThemePreference() string {
	// Check config file
	homeDir, _ := os.UserHomeDir()
	configPath := filepath.Join(homeDir, ".config", "omniagents", "config.yaml")
	
	if data, err := os.ReadFile(configPath); err == nil {
		var config struct {
			Theme string `yaml:"theme"`
		}
		if err := yaml.Unmarshal(data, &config); err == nil && config.Theme != "" {
			return config.Theme
		}
	}

	// Check environment variable
	if theme := os.Getenv("OMNI_THEME"); theme != "" {
		return theme
	}

	// Default
	return "tokyonight"
}

// SaveThemePreference saves the theme preference to config
func SaveThemePreference(theme string) error {
	homeDir, err := os.UserHomeDir()
	if err != nil {
		return err
	}

	configDir := filepath.Join(homeDir, ".config", "omniagents")
	configPath := filepath.Join(configDir, "config.yaml")

	// Ensure directory exists
	if err := os.MkdirAll(configDir, 0755); err != nil {
		return err
	}

	// Read existing config or create new
	var config map[string]interface{}
	if data, err := os.ReadFile(configPath); err == nil {
		yaml.Unmarshal(data, &config)
	}
	if config == nil {
		config = make(map[string]interface{})
	}

	// Update theme
	config["theme"] = theme

	// Write back
	data, err := yaml.Marshal(config)
	if err != nil {
		return err
	}

	return os.WriteFile(configPath, data, 0644)
}