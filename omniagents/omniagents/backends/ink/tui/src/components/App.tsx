/**
 * Optimized main application component
 * Uses atomic state management and optimized components
 */

import React, {
  useState,
  useCallback,
  useEffect,
  useRef,
  useLayoutEffect,
} from "react";
import { Box, Text, useInput, useApp, useStdout, measureElement } from "ink";
import { useRPC } from "../hooks/useRPC.js";
import { useMessagesOptimized } from "../hooks/useMessagesNew.js";
import {
  useMessages,
  useThinking,
  useApproval,
  useMcpApproval,
  useUsage,
  useSessionModel,
  useSessionId,
  useCurrentRunId,
  usePreamble,
  useTasks,
  useBashJobs,
  useActions,
} from "../store/appStore.js";
import type { ApprovalInfo, McpApprovalInfo } from "../store/appStore.js";
import { Header } from "./Header.js";
import { Logo } from "./Logo.js";
import { MessageListOptimized } from "./MessageListOptimized.js";
import { StatusIndicator } from "./StatusIndicator.js";
import { MultiLineInputOptimized } from "./MultiLineInputOptimized.js";
import { ApprovalDialog } from "./ApprovalDialog.js";
import { McpApprovalDialog } from "./McpApprovalDialog.js";
import { Preamble } from "./Preamble.js";
import { Help } from "./Help.js";
import { SessionList } from "./SessionList.js";
import { Scrollable } from "./Scrollable.js";
import { Tasks } from "./Tasks.js";
import { BashJobs } from "./BashJobs.js";
import { MouseProvider } from "../contexts/MouseContext.js";
import { ScrollProvider } from "../contexts/ScrollProvider.js";
import { TokyoNightTheme } from "../theme.js";

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
    return prev.approval.call_id === next.approval.call_id;
  },
);

/**
 * Wrapper for the hosted-MCP approval dialog. Mirrors
 * ApprovalDialogWrapper but renders ``McpApprovalDialog`` and matches on
 * ``request_id`` (not call_id).
 */
const McpApprovalDialogWrapper = React.memo<{
  approval: McpApprovalInfo | null;
  onApprove: (value: "yes" | "no") => void;
}>(
  ({ approval, onApprove }) => {
    if (!approval) {
      return null;
    }
    return (
      <McpApprovalDialog
        tool={approval.tool}
        serverLabel={approval.server_label}
        arguments={approval.arguments}
        onApprove={onApprove}
      />
    );
  },
  (prev, next) => {
    if (prev.approval === next.approval) return true;
    if (!prev.approval || !next.approval) return false;
    return prev.approval.request_id === next.approval.request_id;
  },
);

/**
 * Scrollable message list with keyboard and mouse support
 */
const ScrollableMessageList = React.memo<{
  availableHeight: number;
  onApprove: (value: "yes" | "always" | "no") => void;
  onMcpApprove: (value: "yes" | "no") => void;
}>(
  ({ availableHeight, onApprove, onMcpApprove }) => {
    const messages = useMessages();
    const approval = useApproval();
    const mcpApproval = useMcpApproval();
    const preamble = usePreamble();
    const contentVersion = messages.length;
    return (
      <Scrollable
        hasFocus={true}
        scrollToBottom={true}
        maxHeight={availableHeight}
        flexGrow={1}
        contentVersion={contentVersion}
      >
        <Box flexDirection="column">
          <MessageListOptimized />

          {approval && (
            <Box marginTop={1}>
              <ApprovalDialogWrapper
                approval={approval}
                onApprove={onApprove}
              />
            </Box>
          )}

          {mcpApproval && (
            <Box marginTop={1}>
              <McpApprovalDialogWrapper
                approval={mcpApproval}
                onApprove={onMcpApprove}
              />
            </Box>
          )}

          {preamble && <Preamble content={preamble} />}
        </Box>
      </Scrollable>
    );
  },
  (prev, next) => prev.availableHeight === next.availableHeight,
);

/**
 * Isolate StatusIndicator subscription to prevent parent re-renders
 */
const StatusIndicatorWrapper = React.memo<{
  approval: ApprovalInfo | null;
  thinking: boolean;
}>(
  ({ approval, thinking }) => {
    return (
      <Box paddingX={1} marginTop={1} display={!approval ? "flex" : "none"}>
        <StatusIndicator />
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
  sessionModel: string | null;
  waitingForSecondCtrlC: boolean;
}>(
  ({
    connected,
    thinking,
    approval,
    usage,
    sessionModel,
    waitingForSecondCtrlC,
  }) => {
    return (
      <Box paddingX={1} paddingBottom={1}>
        <Help
          isThinking={thinking}
          isPendingApproval={!!approval}
          connected={connected}
          usage={usage}
          sessionModel={sessionModel}
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
      prev.sessionModel === next.sessionModel &&
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
  enableMouse?: boolean;
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
  enableMouse = true,
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
  const { client, connected, rpcReady, error, sendMessage } = useRPC({
    url: serverUrl,
    token,
    debug,
  });

  // Get state from atomic store
  const messages = useMessages();
  const thinking = useThinking();
  const approval = useApproval();
  const mcpApproval = useMcpApproval();
  const usage = useUsage();
  const sessionModel = useSessionModel();
  const sessionId = useSessionId();
  const currentRunId = useCurrentRunId();
  const tasks = useTasks();
  const bashJobs = useBashJobs();
  const actions = useActions();

  // Measure controls/footer height dynamically (like gemini-cli)
  const controlsRef = useRef<any>(null);
  const [controlsHeight, setControlsHeight] = useState(0);

  // Bash-jobs panel focus: Ctrl+B toggles a "panel mode" that hijacks
  // arrow keys / x / l for navigation + kill + log preview. Esc inside the
  // panel returns to normal typing.
  const [bashPanelFocused, setBashPanelFocused] = useState(false);

  // Warm up the server-side bash-jobs sweeper as soon as a running job
  // appears in the panel. Without this, a job spawned by the agent that
  // then naturally exits while the user is idle would leave the panel
  // stuck on "running" — the sweeper needs a service handle, captured on
  // the first server-function call. One read-only RPC is enough.
  const bashJobsWarmupRef = useRef(false);
  const anyBashJobRunning = bashJobs.some((j) => j.running);
  useEffect(() => {
    if (!anyBashJobRunning) {
      bashJobsWarmupRef.current = false;
      return;
    }
    if (bashJobsWarmupRef.current || !client || !connected) return;
    bashJobsWarmupRef.current = true;
    client
      .serverCall("bash_jobs.list", {}, sessionId || undefined)
      .catch(() => {
        bashJobsWarmupRef.current = false;
      });
  }, [anyBashJobRunning, client, connected, sessionId]);

  useLayoutEffect(() => {
    if (controlsRef.current) {
      const measurement = measureElement(controlsRef.current);
      if (measurement.height > 0) {
        setControlsHeight(measurement.height);
      }
    }
  }, [terminalHeight, approval, mcpApproval, thinking, tasks]);

  // Calculate available height for messages (gemini-cli approach)
  const availableTerminalHeight = Math.max(
    5,
    terminalHeight - controlsHeight - 2,
  );

  // Ctrl+C handling - require double tap to quit
  const { exit } = useApp();
  const ctrlCCount = useRef(0);
  const ctrlCTimer = useRef<NodeJS.Timeout | null>(null);
  const [waitingForSecondCtrlC, setWaitingForSecondCtrlC] = useState(false);

  // Setup message handlers with batching
  const { approveToolCall, approveMcpToolCall, cancelRun } =
    useMessagesOptimized({
      client,
      sessionId: sessionId ?? initialSessionId,
      rpcReady,
    });

  // Set initial session ID
  useEffect(() => {
    if (initialSessionId) {
      actions.setSessionId(initialSessionId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSessionId]);

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

  // Reflect connection status into status indicator like bubbletea
  useEffect(() => {
    if (uiState !== "chat") return;
    if (!connected) {
      actions.setStatus("Disconnected from server", "error", false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected, uiState]);

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [availableSessions, selectedSessionIndex, debug],
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

  // Hosted-MCP approval — parallel handler. No "always" affordance.
  const handleMcpApproval = useCallback(
    (value: "yes" | "no") => {
      approveMcpToolCall(value === "yes");
    },
    [approveMcpToolCall],
  );

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

      // Built-in /help removed; allow server function 'help' or fallback

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
          // Tag the freshly-minted id so useMessagesNew skips its
          // history-load fetch — the server may not have persisted the
          // user message yet, and an empty fetch would clobber the
          // optimistic addMessage above.
          actions.markSessionAsMinted(response.session_id);
          actions.setSessionId(response.session_id);
        }
      } catch (err) {
        // Show error in status indicator instead of just logging
        const errorMessage =
          err instanceof Error ? err.message : "Failed to send message";
        actions.setThinking(false, `Error: ${errorMessage}`);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [client, connected, sendMessage, sessionId, handleSlashCommand],
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
        // Ctrl+B: enter the bash-jobs panel for keyboard management. Only
        // toggles on when there's at least one job; otherwise it's a no-op
        // so the chord doesn't feel broken when the panel is hidden.
        if (key.ctrl && input === "b") {
          if (!bashPanelFocused && bashJobs.length > 0) {
            setBashPanelFocused(true);
          } else if (bashPanelFocused) {
            setBashPanelFocused(false);
          }
          return;
        }
        // While the panel owns input, swallow other keys here so they
        // don't double-handle (the panel registers its own useInput).
        if (bashPanelFocused) {
          return;
        }
        if (key.escape) {
          if (approval) {
            handleApproval("no");
          } else if (mcpApproval) {
            handleMcpApproval("no");
          } else if (thinking && currentRunId) {
            cancelRun(currentRunId);
          }
          return;
        }
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
            } else if (mcpApproval) {
              handleMcpApproval("no");
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
      [
        thinking,
        currentRunId,
        approval,
        mcpApproval,
        cancelRun,
        handleApproval,
        handleMcpApproval,
        exit,
        bashPanelFocused,
        bashJobs.length,
      ],
    ),
    { isActive: uiState === "chat" || !!approval || !!mcpApproval },
  );

  // If every job vanishes (cleared, archived) while the panel is focused,
  // fall back to normal typing — there's nothing to manage.
  useEffect(() => {
    if (bashPanelFocused && bashJobs.length === 0) {
      setBashPanelFocused(false);
    }
  }, [bashPanelFocused, bashJobs.length]);

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
        <Text color={TokyoNightTheme.defaultText}>
          Connecting to {agentName}...
        </Text>
      </Box>
    );
  }

  if (uiState === "error" || (error && !connected)) {
    return (
      <Box flexDirection="column">
        <Text color={TokyoNightTheme.error}>Failed to connect to server:</Text>
        <Text color={TokyoNightTheme.defaultText}>
          {error?.message || "Unknown error"}
        </Text>
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
          <Logo agentName={agentName} />
        </Box>
        <Box marginTop={1}>
          <SessionList
            sessions={availableSessions}
            selectedIndex={selectedSessionIndex}
          />
        </Box>
        {debug && (
          <Box paddingX={1} marginTop={1}>
            <Text color={TokyoNightTheme.defaultText}>
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
    <MouseProvider mouseEventsEnabled={!!enableMouse}>
      <ScrollProvider>
        <Box flexDirection="column">
          {/* Header with logo and welcome message - visible only before first message */}
          <Header
            agentName={agentName}
            welcomeText={welcomeText}
            visible={messages.length === 0}
          />

          {/* Message history - scrollable with keyboard/mouse support */}
          <Box paddingX={1} marginTop={1} flexGrow={1}>
            <ScrollableMessageList
              availableHeight={availableTerminalHeight}
              onApprove={handleApproval}
              onMcpApprove={handleMcpApproval}
            />
          </Box>

          {/* Controls/footer section - measured for dynamic height calculation */}
          <Box ref={controlsRef} flexDirection="column">
            {/* Status indicator - ALWAYS in DOM, just hidden - isolated subscription */}
            <StatusIndicatorWrapper approval={approval} thinking={thinking} />

            {/* Docked task list - renders nothing when empty */}
            <Tasks />

            {/* Docked background-bash-jobs panel - renders nothing when empty */}
            <BashJobs
              focused={bashPanelFocused}
              onUnfocus={() => setBashPanelFocused(false)}
              onKill={async (job_id) => {
                if (!client) return {};
                return await client.serverCall(
                  "bash_jobs.kill",
                  { job_id },
                  sessionId || undefined,
                );
              }}
              onTail={async (job_id, lines) => {
                if (!client) return { ok: false, error: "no client" };
                const args: Record<string, unknown> = { job_id };
                if (typeof lines === "number") args.lines = lines;
                return (await client.serverCall(
                  "bash_jobs.tail",
                  args,
                  sessionId || undefined,
                )) as any;
              }}
            />

            {/* Input prompt - always rendered, disabled during approval/thinking */}
            <Box marginTop={1}>
              <MultiLineInputOptimized
                onSubmit={handleSubmit}
                disabled={
                  !connected || !!approval || !!mcpApproval || bashPanelFocused
                }
                disableSubmit={thinking}
                history={messageHistory}
              />
            </Box>

            {/* Help text below input - isolated subscription */}
            <HelpWrapper
              connected={connected}
              thinking={thinking}
              approval={approval}
              usage={usage}
              sessionModel={sessionModel}
              waitingForSecondCtrlC={waitingForSecondCtrlC}
            />
          </Box>
        </Box>
      </ScrollProvider>
    </MouseProvider>
  );
};
