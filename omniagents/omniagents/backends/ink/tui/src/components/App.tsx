/**
 * Optimized main application component
 * Uses atomic state management and optimized components
 */

import React, { useState, useCallback, useEffect, useRef } from "react";
import { Box, Text, useInput, useApp, useStdout } from "ink";
import { useRPC } from "../hooks/useRPC.js";
import { useMessagesOptimized } from "../hooks/useMessagesNew.js";
import {
  useMessages,
  useThinking,
  useApproval,
  useUsage,
  useSessionId,
  useCurrentRunId,
  useActions,
} from "../store/appStore.js";
import type { ApprovalInfo } from "../store/appStore.js";
import { StatusBar } from "./StatusBar.js";
import { MessageListOptimized } from "./MessageListOptimized.js";
import { ToolActivityOptimized } from "./ToolActivityOptimized.js";
import { MultiLineInputOptimized } from "./MultiLineInputOptimized.js";
import { ApprovalDialog } from "./ApprovalDialog.js";
import { Help } from "./Help.js";
import { SessionList } from "./SessionList.js";
import { Scrollable } from "./Scrollable.js";
import { MouseProvider } from "../contexts/MouseContext.js";
import { ScrollProvider } from "../contexts/ScrollProvider.js";

/**
 * Wrapper to keep ApprovalDialog mounted to prevent flicker
 */
const ApprovalDialogWrapper = React.memo<{
  approval: ApprovalInfo | null;
  onApprove: (value: "yes" | "always" | "no") => void;
}>(
  ({ approval, onApprove }) => {
    if (!approval) {
      // Render empty placeholder to keep component mounted
      return null;
    }

    return (
      <ApprovalDialog
        tool={approval.tool}
        arguments={approval.arguments}
        metadata={approval.metadata}
        onApprove={onApprove}
      />
    );
  },
  (prev, next) => {
    // Only re-render if approval actually changed
    if (prev.approval === next.approval) return true;
    if (!prev.approval || !next.approval) return false;
    return prev.approval.request_id === next.approval.request_id;
  },
);

/**
 * Scrollable message list with keyboard and mouse support
 */
const ScrollableMessageList = React.memo<{ terminalHeight: number }>(
  ({ terminalHeight }) => {
    // Calculate available height for messages
    // Reserve space for:
    // - StatusBar (1 line)
    // - ToolActivity/Approval (3 lines max)
    // - Input prompt (3 lines)
    // - Help text (2 lines)
    // - Margins and spacing (3 lines)
    const reservedLines = 12;
    const maxHeight = Math.max(5, terminalHeight - reservedLines);

    return (
      <Scrollable
        hasFocus={true}
        scrollToBottom={true}
        maxHeight={maxHeight}
        flexGrow={1}
      >
        <Box flexDirection="column">
          <MessageListOptimized />
        </Box>
      </Scrollable>
    );
  },
  (prev, next) => prev.terminalHeight === next.terminalHeight,
);

/**
 * Isolate ToolActivity subscription to prevent parent re-renders
 */
const ToolActivityWrapper = React.memo<{
  approval: ApprovalInfo | null;
  thinking: boolean;
}>(
  ({ approval, thinking }) => {
    return (
      <Box paddingX={1} display={!approval && thinking ? "flex" : "none"}>
        <ToolActivityOptimized />
      </Box>
    );
  },
  (prev, next) => {
    return prev.approval === next.approval && prev.thinking === next.thinking;
  },
);

/**
 * Isolate Help subscription to prevent parent re-renders
 */
const HelpWrapper = React.memo<{
  connected: boolean;
  thinking: boolean;
  approval: ApprovalInfo | null;
  usage: ReturnType<typeof useUsage>;
  waitingForSecondCtrlC: boolean;
}>(
  ({ connected, thinking, approval, usage, waitingForSecondCtrlC }) => {
    return (
      <Box paddingX={1} paddingBottom={1}>
        <Help
          isThinking={thinking}
          isPendingApproval={!!approval}
          connected={connected}
          usage={usage}
          waitingForSecondCtrlC={waitingForSecondCtrlC}
        />
      </Box>
    );
  },
  (prev, next) => {
    return (
      prev.connected === next.connected &&
      prev.thinking === next.thinking &&
      prev.approval === next.approval &&
      prev.usage === next.usage &&
      prev.waitingForSecondCtrlC === next.waitingForSecondCtrlC
    );
  },
);

export interface AppProps {
  serverUrl: string;
  agentName: string;
  token?: string;
  debug?: boolean;
  sessionId?: string;
  resumeMode?: boolean;
  welcomeText?: string;
  initialMessage?: string;
}

// UI state machine for proper flow control
type UIState = "connecting" | "session_select" | "chat" | "error";

export const App: React.FC<AppProps> = ({
  serverUrl,
  agentName,
  token,
  debug,
  sessionId: initialSessionId,
  resumeMode = false,
  welcomeText,
  initialMessage,
}) => {
  const [messageHistory, setMessageHistory] = useState<string[]>([]);
  const [uiState, setUIState] = useState<UIState>("connecting");
  const [availableSessions, setAvailableSessions] = useState<
    Array<{
      id: string;
      created_at: string;
      archived: boolean;
      message_count: number;
      first_message?: unknown;
      last_message?: unknown;
    }>
  >([]);
  const [selectedSessionIndex, setSelectedSessionIndex] = useState(0);

  // Get terminal dimensions for scrolling calculations
  const { stdout } = useStdout();
  const terminalHeight = stdout?.rows || 24;

  // RPC connection
  const { client, connected, rpcReady, connecting, error, sendMessage } =
    useRPC({
      url: serverUrl,
      token,
      debug,
    });

  // Get state from atomic store
  const messages = useMessages();
  const thinking = useThinking();
  const approval = useApproval();
  const usage = useUsage();
  const sessionId = useSessionId();
  const currentRunId = useCurrentRunId();
  const actions = useActions();

  // Ctrl+C handling - require double tap to quit
  const { exit } = useApp();
  const ctrlCCount = useRef(0);
  const ctrlCTimer = useRef<NodeJS.Timeout | null>(null);
  const [waitingForSecondCtrlC, setWaitingForSecondCtrlC] = useState(false);

  // Setup message handlers with batching
  const { approveToolCall, cancelRun } = useMessagesOptimized({
    client,
    sessionId: sessionId ?? initialSessionId,
  });

  // Set initial session ID
  useEffect(() => {
    if (initialSessionId) {
      actions.setSessionId(initialSessionId);
    }
  }, [initialSessionId, actions]);

  // Load global user history from server when RPC is ready
  useEffect(() => {
    if (!client || !rpcReady) {
      return;
    }

    // Fetch global history (last 200 user messages across all sessions)
    const loadHistory = async () => {
      try {
        const items = await client.getUserHistory(200, true);

        // Extract content and deduplicate
        const historyItems: string[] = [];
        const seen = new Set<string>();

        for (const item of items) {
          const content = item.content.trim();
          if (content && !seen.has(content)) {
            seen.add(content);
            historyItems.push(content);
          }
        }

        // Set as initial history
        setMessageHistory(historyItems);
      } catch (err) {
        if (debug) {
          console.error("Failed to load global history:", err);
        }
        // Don't fail - just continue without global history
      }
    };

    loadHistory();
  }, [client, rpcReady, debug]);

  // Load sessions for resume mode (wait for RPC ready to avoid race condition)
  useEffect(() => {
    if (!client || !rpcReady) {
      return;
    }

    // If resume mode and no specific session ID, show session list
    if (resumeMode && !initialSessionId) {
      const loadSessions = async () => {
        try {
          if (debug) {
            console.error("[DEBUG] Fetching sessions for resume mode...");
          }
          const sessions = await client.listSessions();
          if (debug) {
            console.error(`[DEBUG] Fetched ${sessions?.length || 0} sessions`);
          }
          if (sessions && sessions.length > 0) {
            setAvailableSessions(sessions);
            setUIState("session_select");
            if (debug) {
              console.error("[DEBUG] UI state: session_select");
            }
          } else {
            // No sessions - go directly to chat
            setUIState("chat");
            if (debug) {
              console.error("[DEBUG] No sessions found, UI state: chat");
            }
          }
        } catch (err) {
          console.error("Failed to load sessions:", err);
          // On error, just start fresh chat
          setUIState("chat");
        }
      };

      loadSessions();
    } else if (rpcReady && !resumeMode) {
      // Not in resume mode, go straight to chat
      setUIState("chat");
      if (debug) {
        console.error("[DEBUG] Not resume mode, UI state: chat");
      }
    } else if (rpcReady && initialSessionId) {
      // Have a specific session ID, go straight to chat
      setUIState("chat");
      if (debug) {
        console.error("[DEBUG] Have session ID, UI state: chat");
      }
    }
  }, [client, rpcReady, resumeMode, initialSessionId, debug]);

  // Handle session list navigation
  const handleSessionListInput = useCallback(
    (input: string, key: any) => {
      if (debug) {
        console.error("[DEBUG] Session list input:", {
          input,
          key: Object.keys(key).filter((k) => key[k]),
        });
      }

      if (key.upArrow) {
        setSelectedSessionIndex((prev) => Math.max(0, prev - 1));
      } else if (key.downArrow) {
        setSelectedSessionIndex((prev) =>
          Math.min(availableSessions.length - 1, prev + 1),
        );
      } else if (key.return) {
        // Select session and transition to chat
        const selected = availableSessions[selectedSessionIndex];
        if (selected) {
          if (debug) {
            console.error("[DEBUG] Selected session:", selected.id);
          }
          actions.setSessionId(selected.id);
          setUIState("chat");
          if (debug) {
            console.error("[DEBUG] UI state: chat (session selected)");
          }
        }
      } else if (key.tab) {
        // Create new session and transition to chat
        if (debug) {
          console.error("[DEBUG] Creating new session (Tab pressed)");
        }
        setUIState("chat");
        if (debug) {
          console.error("[DEBUG] UI state: chat (new session)");
        }
      }
    },
    [availableSessions, selectedSessionIndex, actions, debug],
  );

  // Handle approval
  const handleApproval = useCallback(
    (value: "yes" | "always" | "no") => {
      const approved = value !== "no";
      const alwaysApprove = value === "always";
      approveToolCall(approved, alwaysApprove);
    },
    [approveToolCall],
  );

  // Handle Escape key for cancellation (passed to child components to avoid parent re-renders)
  const handleEscape = useCallback(() => {
    if (approval) {
      // During approval: Deny
      handleApproval("no");
    } else if (thinking && currentRunId) {
      // During execution: Cancel
      cancelRun(currentRunId);
    }
  }, [approval, thinking, currentRunId, handleApproval, cancelRun]);

  // Handle slash commands
  const handleSlashCommand = useCallback(
    async (cmd: string): Promise<{ sendToLLM: boolean; result?: string }> => {
      const parts = cmd.trim().split(/\s+/);
      if (parts.length === 0) {
        return { sendToLLM: false };
      }

      const name = parts[0].toLowerCase();
      const argText = cmd.slice(parts[0].length).trim();

      // Built-in: /exit
      if (name === "/exit") {
        exit();
        return { sendToLLM: false };
      }

      // Built-in: /help
      if (name === "/help") {
        try {
          const funcs = await client!.listServerFunctions();
          let helpText = "Available commands:\n\n";
          helpText += "- /exit – Quit\n";

          for (const f of funcs) {
            const alias = "/" + f.name;
            let argHint = "";
            if (
              f.params_schema?.properties &&
              "text" in f.params_schema.properties
            ) {
              argHint = " <text>";
            }
            const desc = f.description || `Invoke server function '${f.name}'`;
            helpText += `- ${alias}${argHint} – ${desc}\n`;
          }

          return { sendToLLM: false, result: helpText.trimEnd() };
        } catch (err) {
          return {
            sendToLLM: false,
            result: `Error listing server functions: ${err}`,
          };
        }
      }

      // Server function invocation
      try {
        const fn = name.slice(1); // Remove leading /

        // Check if this server function exists
        const funcs = await client!.listServerFunctions();
        const found = funcs.find(
          (f) => f.name.toLowerCase() === fn.toLowerCase(),
        );

        if (!found) {
          // Not a server function - fall back to LLM
          return { sendToLLM: true };
        }

        // Parse arguments
        let args: Record<string, unknown> = {};
        if (argText) {
          try {
            const parsed = JSON.parse(argText);
            if (typeof parsed === "object" && !Array.isArray(parsed)) {
              args = parsed;
            } else if (Array.isArray(parsed)) {
              args = { args: parsed };
            } else if (typeof parsed === "string") {
              args = { text: parsed };
            } else {
              args = { value: parsed };
            }
          } catch {
            // Not JSON - treat as text argument
            args = { text: argText };
          }
        }

        // Call server function
        const result = await client!.serverCall(
          fn,
          args,
          sessionId || undefined,
        );

        // Format result
        if (result.message && typeof result.message === "string") {
          return { sendToLLM: false, result: result.message };
        }

        const formatted = JSON.stringify(result, null, 2);
        return {
          sendToLLM: false,
          result:
            formatted === "null" || !formatted.trim() ? "Done." : formatted,
        };
      } catch (err) {
        return { sendToLLM: false, result: `Error: ${err}` };
      }
    },
    [client, sessionId, exit],
  );

  // Handle user input
  const handleSubmit = useCallback(
    async (message: string) => {
      if (!client || !connected) {
        return;
      }

      // Check for slash command
      if (message.startsWith("/")) {
        const { sendToLLM, result } = await handleSlashCommand(message);

        if (!sendToLLM) {
          // Show result as assistant message (if any)
          if (result) {
            actions.addMessage({
              role: "assistant",
              content: result,
              timestamp: new Date(),
            });
          }
          return;
        }
        // Fall through to send to LLM if not handled
      }

      // Add user message to UI immediately
      actions.addMessage({
        role: "user",
        content: message,
        timestamp: new Date(),
      });

      // Add to history (avoid duplicates)
      setMessageHistory((prev) => {
        if (prev.length === 0 || prev[prev.length - 1] !== message) {
          return [...prev, message];
        }
        return prev;
      });

      try {
        // Send to server and get session ID back
        const response = await sendMessage(message, sessionId);
        if (!sessionId) {
          actions.setSessionId(response.session_id);
        }
      } catch (err) {
        console.error("Failed to send message:", err);
      }
    },
    [client, connected, sendMessage, sessionId, actions, handleSlashCommand],
  );

  // Handle session list input (only when in session_select state)
  useInput(
    (input, key) => {
      if (debug) {
        console.error("[DEBUG] useInput triggered:", {
          input,
          key: Object.keys(key).filter((k) => key[k]),
          uiState,
          isActive: uiState === "session_select",
        });
      }
      if (uiState === "session_select") {
        handleSessionListInput(input, key);
      }
    },
    { isActive: uiState === "session_select" },
  );

  // Handle Ctrl+C - double tap to quit (like bubbletea)
  // Only active in chat mode to avoid conflicts with session selection
  useInput(
    useCallback(
      (input, key) => {
        if (key.ctrl && input === "c") {
          ctrlCCount.current++;

          // Clear existing timer
          if (ctrlCTimer.current) {
            clearTimeout(ctrlCTimer.current);
          }

          if (ctrlCCount.current === 1) {
            // First Ctrl+C: Cancel current run if active
            if (thinking && currentRunId) {
              cancelRun(currentRunId);
            } else if (approval) {
              handleApproval("no");
            }

            // Show warning message
            setWaitingForSecondCtrlC(true);

            // Reset counter and hide warning after 1 second
            ctrlCTimer.current = setTimeout(() => {
              ctrlCCount.current = 0;
              setWaitingForSecondCtrlC(false);
            }, 1000);
          } else if (ctrlCCount.current >= 2) {
            // Second Ctrl+C within 1 second: Exit
            setWaitingForSecondCtrlC(false);
            exit();
          }
        }
      },
      [thinking, currentRunId, approval, cancelRun, handleApproval, exit],
    ),
    { isActive: true }, // Always active for emergency exit
  );

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (ctrlCTimer.current) {
        clearTimeout(ctrlCTimer.current);
      }
    };
  }, []);

  // Send initial message if provided (only in chat state)
  useEffect(() => {
    if (initialMessage && uiState === "chat" && messages.length === 0) {
      handleSubmit(initialMessage);
    }
  }, [initialMessage, uiState, messages.length, handleSubmit]);

  // Render based on UI state
  if (uiState === "connecting") {
    return (
      <Box flexDirection="column">
        <Text>Connecting to {agentName}...</Text>
      </Box>
    );
  }

  if (uiState === "error" || (error && !connected)) {
    return (
      <Box flexDirection="column">
        <Text color="red">Failed to connect to server:</Text>
        <Text>{error?.message || "Unknown error"}</Text>
      </Box>
    );
  }

  if (uiState === "session_select") {
    if (debug) {
      console.error(
        `[DEBUG] Rendering session_select state with ${availableSessions.length} sessions, selectedIndex=${selectedSessionIndex}`,
      );
    }
    return (
      <Box flexDirection="column">
        <Box paddingX={1}>
          <StatusBar
            agentName={agentName}
            connected={connected}
            sessionId={sessionId}
          />
        </Box>
        <Box marginTop={1}>
          <SessionList
            sessions={availableSessions}
            selectedIndex={selectedSessionIndex}
          />
        </Box>
        {debug && (
          <Box paddingX={1} marginTop={1}>
            <Text dimColor>
              DEBUG: State={uiState}, Selected={selectedSessionIndex}/
              {availableSessions.length}
            </Text>
          </Box>
        )}
      </Box>
    );
  }

  // Chat UI (uiState === 'chat')
  return (
    <MouseProvider>
      <ScrollProvider>
        <Box flexDirection="column">
          {/* Status bar - always visible */}
          <Box paddingX={1}>
            <StatusBar
              agentName={agentName}
              connected={connected}
              sessionId={sessionId}
            />
          </Box>

          {/* Welcome message - keep in DOM, control visibility */}
          {welcomeText && (
            <Box
              paddingY={1}
              marginTop={1}
              paddingX={1}
              display={messages.length === 0 ? "flex" : "none"}
            >
              <Text>{welcomeText}</Text>
            </Box>
          )}

          {/* Message history - scrollable with keyboard/mouse support */}
          <Box paddingX={1} flexGrow={1}>
            <ScrollableMessageList terminalHeight={terminalHeight} />
          </Box>

          {/* Tool activity - ALWAYS in DOM, just hidden - isolated subscription */}
          <ToolActivityWrapper approval={approval} thinking={thinking} />

          {/* Approval dialog - ALWAYS in DOM with placeholder to prevent mount/unmount */}
          <Box marginTop={1} paddingX={1} display={approval ? "flex" : "none"}>
            <ApprovalDialogWrapper
              approval={approval}
              onApprove={handleApproval}
            />
          </Box>

          {/* Input prompt - always rendered, disabled during approval/thinking */}
          <Box marginTop={1}>
            <MultiLineInputOptimized
              onSubmit={handleSubmit}
              onEscape={handleEscape}
              disabled={!connected || thinking || !!approval}
              history={messageHistory}
            />
          </Box>

          {/* Help text below input - isolated subscription */}
          <HelpWrapper
            connected={connected}
            thinking={thinking}
            approval={approval}
            usage={usage}
            waitingForSecondCtrlC={waitingForSecondCtrlC}
          />
        </Box>
      </ScrollProvider>
    </MouseProvider>
  );
};
