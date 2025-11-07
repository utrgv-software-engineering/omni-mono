/**
 * TokyoNight theme colors - matching Bubbletea theme exactly
 */

export const TokyoNightTheme = {
  // Message roles
  userMessage: "#bb9af7", // Purple (matching prompt indicator)
  assistantMessage: "#7aa2f7", // Soft blue

  // Tool colors
  toolName: "#7dcfff", // Cyan
  toolResult: "#565f89", // Muted gray-blue

  // UI elements
  border: "#3b4261", // Subtle gray
  inputPrompt: "#bb9af7", // Purple
  messagePrompt: "#bb9af7", // Purple (for > prefix)

  // Status colors
  error: "#f7768e", // Soft red
  warning: "#e0af68", // Warm yellow
  success: "#9ece6a", // Green
  info: "#7dcfff", // Cyan

  // Approval dialog
  approvalTitle: "#e0af68", // Warning yellow
  approvalSelected: "#7aa2f7", // Blue
  approvalUnselected: "#565f89", // Muted gray-blue

  // Session display
  sessionTitle: "#7aa2f7", // Blue for selected session
  sessionSubtitle: "#565f89", // Muted for session info

  // Help text
  help: "#565f89", // Muted gray-blue for help
  inputText: "#c0caf5", // Default text color
} as const;
