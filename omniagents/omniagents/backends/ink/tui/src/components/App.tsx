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
import { Box, Text, useApp, useStdout, measureElement } from "ink";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import { useRPC } from "../hooks/useRPC.js";
import { useMessagesOptimized } from "../hooks/useMessagesNew.js";
import {
  useMessages,
  useThinking,
  useStatus,
  useQueuedMessages,
  useApproval,
  useMcpApproval,
  useEscalation,
  useNotifications,
  useUsage,
  useSessionModel,
  useSessionId,
  useCurrentRunId,
  usePreamble,
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
import { Workers } from "./Workers.js";
import { BashJobs } from "./BashJobs.js";
import { QueuedMessages } from "./QueuedMessages.js";
import { Goal } from "./Goal.js";
import { Wakeup } from "./Wakeup.js";
import { Loop } from "./Loop.js";
import { Recap } from "./Recap.js";
import { EscalationBanner } from "./EscalationBanner.js";
import { Notifications } from "./Notifications.js";
import { resolveEscalation } from "../hooks/escalationBridge.js";
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
 * Inline "⠋ Thinking…" / status indicator rendered at the bottom of the
 * message transcript (above the dock panels). Reads its own subscriptions
 * so the parent doesn't have to re-render when thinking/status flips, and
 * returns null when there's nothing to show — no phantom row when idle.
 *
 * Hidden while an approval dialog is up: the approval surface itself
 * communicates the paused state.
 */
const InlineStatusIndicator = React.memo(() => {
  const thinking = useThinking();
  const statusObj = useStatus() as unknown as { status: string | null };
  const approval = useApproval();
  const mcpApproval = useMcpApproval();
  if (approval || mcpApproval) return null;
  if (!thinking && !statusObj?.status) return null;
  // No margins: the parent column owns spacing via gap={1}. This
  // applies to every element in the scroll region — messages, status,
  // preamble, dock panels, approval surfaces.
  return <StatusIndicator />;
});

/**
 * Scrollable message list with keyboard and mouse support.
 *
 * Layout inside the scroll region (top-to-bottom):
 *   1. MessageListOptimized — full transcript
 *   2. InlineStatusIndicator — "⠋ Thinking…" / status line, only when active
 *   3. Preamble — animated streaming agent text, co-located with status
 *   4. ``children`` — dock panels (Goal/Tasks/Workers/QueuedMessages/
 *      Notifications/BashJobs/EscalationBanner) passed in from App
 *   5. Approval / MCP approval dialog, when present
 *
 * Pin-to-bottom keeps the newest content visible by default; scrolling
 * up via mouse wheel or shift+↑/↓ clears the pin (see Scrollable.tsx).
 */
const ScrollableMessageList: React.FC<{
  availableHeight: number;
  onApprove: (value: "yes" | "always" | "no") => void;
  onMcpApprove: (value: "yes" | "no") => void;
  children?: React.ReactNode;
}> = ({ availableHeight, onApprove, onMcpApprove, children }) => {
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
      {/* Outer gap={1}: 1-row rhythm between transcript, Thinking…,
          Preamble, the panel group, and approvals. Inner gap={0} on
          the panel-group wrapper lets bordered panels stack with
          their borders touching (no empty row between them). Children
          must not carry their own marginTop / marginBottom. */}
      <Box flexDirection="column" gap={1}>
        <MessageListOptimized />

        <InlineStatusIndicator />

        {preamble && <Preamble content={preamble} />}

        <Box flexDirection="column" gap={0}>
          {children}
        </Box>

        {approval && (
          <ApprovalDialogWrapper approval={approval} onApprove={onApprove} />
        )}

        {mcpApproval && (
          <McpApprovalDialogWrapper
            approval={mcpApproval}
            onApprove={onMcpApprove}
          />
        )}
      </Box>
    </Scrollable>
  );
};

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
  const queuedMessages = useQueuedMessages();
  const approval = useApproval();
  const mcpApproval = useMcpApproval();
  const escalation = useEscalation();
  const notifications = useNotifications();
  const usage = useUsage();
  const sessionModel = useSessionModel();
  const sessionId = useSessionId();
  const currentRunId = useCurrentRunId();
  const bashJobs = useBashJobs();
  const actions = useActions();

  // Measure controls/footer height dynamically (like gemini-cli)
  const controlsRef = useRef<any>(null);
  const [controlsHeight, setControlsHeight] = useState(0);

  // Notifications panel focus: Ctrl+N toggles the panel and lets the
  // user dismiss individual / all queued notifications. Mirrors the
  // Ctrl+B bash-jobs chord.
  const [notificationsPanelFocused, setNotificationsPanelFocused] =
    useState(false);

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

  // Seed the goal panel on connect / session-switch. The /goal autopilot
  // loop broadcasts ui.goal.update on every state change, but if a goal
  // is already running when the TUI reconnects we'd otherwise show an
  // empty panel until the next turn boundary. One read-only RPC fills it.
  const goalSeedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!client || !connected || !sessionId) return;
    if (goalSeedRef.current === sessionId) return;
    goalSeedRef.current = sessionId;
    client
      .serverCall("goal.status", {}, sessionId)
      .then((res) => {
        const snap = (res as { snapshot?: unknown } | undefined)?.snapshot;
        if (snap === null || snap === undefined) {
          actions.setGoal(null);
        } else if (typeof snap === "object") {
          actions.setGoal(snap as any);
        }
      })
      .catch(() => {
        // goal.status may not be registered on every agent — silently
        // ignore so the panel just stays empty.
        goalSeedRef.current = null;
      });
  }, [client, connected, sessionId, actions]);

  // Seed the wakeup panel on connect / session-switch. Same rationale
  // as the goal seed above — picks up an active schedule that was
  // already running before we attached. wakeup.status may not be
  // registered on every agent; silently ignore so the panel stays empty.
  const wakeupSeedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!client || !connected || !sessionId) return;
    if (wakeupSeedRef.current === sessionId) return;
    wakeupSeedRef.current = sessionId;
    client
      .serverCall("wakeup.status", {}, sessionId)
      .then((res) => {
        const snap = (res as { snapshot?: unknown } | undefined)?.snapshot;
        if (snap === null || snap === undefined) {
          actions.setWakeup(null);
        } else if (typeof snap === "object") {
          actions.setWakeup(snap as any);
        }
      })
      .catch(() => {
        wakeupSeedRef.current = null;
      });
  }, [client, connected, sessionId, actions]);

  const loopSeedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!client || !connected || !sessionId) return;
    if (loopSeedRef.current === sessionId) return;
    loopSeedRef.current = sessionId;
    client
      .serverCall("loop.status", {}, sessionId)
      .then((res) => {
        const snap = (res as { snapshot?: unknown } | undefined)?.snapshot;
        if (Array.isArray(snap)) {
          actions.setLoops(snap as any);
        }
      })
      .catch(() => {
        loopSeedRef.current = null;
      });
  }, [client, connected, sessionId, actions]);

  // Seed the workers panel on connect / session-switch. Same rationale
  // as the goal seed above — picks up workers that were already running
  // before we attached. workers.list may not be registered on every
  // agent; silently ignore so the panel stays empty.
  const workersSeedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!client || !connected || !sessionId) return;
    if (workersSeedRef.current === sessionId) return;
    workersSeedRef.current = sessionId;
    client
      .serverCall("workers.list", {}, sessionId)
      .then((res) => {
        const snap = (res as { snapshot?: unknown } | undefined)?.snapshot;
        if (Array.isArray(snap)) {
          actions.setWorkers(snap as any);
        } else {
          actions.setWorkers([]);
        }
      })
      .catch(() => {
        workersSeedRef.current = null;
      });
  }, [client, connected, sessionId, actions]);

  // Reset dock dismissal sets on session change so the freshly bound
  // session starts with the panels showing their full server-side
  // snapshot.
  useEffect(() => {
    actions.resetDockDismissals();
  }, [sessionId, actions]);

  useLayoutEffect(() => {
    if (controlsRef.current) {
      const measurement = measureElement(controlsRef.current);
      if (measurement.height > 0) {
        setControlsHeight(measurement.height);
      }
    }
  }, [terminalHeight, approval, mcpApproval, thinking]);

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
          // Not a server function - fall back to LLM. sendMessage
          // (useRPC) mints a session on first call, so we don't need to
          // pre-ensure here.
          return { sendToLLM: true };
        }

        // Anchor a session before dispatching the server function.
        // Slash commands like /goal short-circuit sendMessage's
        // mint-on-first-call (which only runs for regular chat), so when
        // /goal is the user's very first action there's no sessionId to
        // pass — the function fires session-less and goal returns
        // "No session context". session.ensure({}) mints one on the
        // server and returns its id; we mirror it into local store so
        // subsequent UI state stays consistent.
        let sid: string | undefined = sessionId || undefined;
        if (!sid) {
          try {
            const ensured = (await client!.serverCall("session.ensure", {})) as
              | { session_id?: unknown }
              | undefined;
            const ensuredId =
              typeof ensured?.session_id === "string"
                ? ensured.session_id
                : undefined;
            if (ensuredId) {
              sid = ensuredId;
              actions.setSessionId(ensuredId);
            }
          } catch {
            /* best-effort — let the server function attempt the call anyway */
          }
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
        const result = await client!.serverCall(fn, args, sid);

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
    [client, sessionId, exit, actions],
  );

  // Handle user input
  const handleSubmit = useCallback(
    async (message: string) => {
      if (!client || !connected) {
        return;
      }

      // Snapshot every currently-terminal dock item into the dismissed
      // sets so the next run starts with a clean dock. Items spawned by
      // the new run aren't in the set yet and render normally; their
      // IDs land in the set on the *next* submit. Fires for slash
      // commands too — a slash is still a user step boundary.
      actions.dismissDockTerminals();

      // Escalation interception: when the agent has called ``escalate``,
      // route the user's next message back as the tool's reply via
      // client_response (and clear the banner) instead of starting a
      // new run. Slash commands still pass through to the slash handler
      // — they're an opt-out for users who want to issue /goal.stop or
      // /help during an escalation rather than reply to it.
      if (escalation && !message.startsWith("/")) {
        const resolved = resolveEscalation(escalation.request_id, message);
        actions.setEscalation(null);
        if (resolved) {
          actions.addMessage({
            role: "user",
            content: message,
            timestamp: new Date(),
          });
        }
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

      // Always update input-recall history (regardless of which path
      // the submit takes — queued or direct).
      setMessageHistory((prev) => {
        if (prev.length === 0 || prev[prev.length - 1] !== message) {
          return [...prev, message];
        }
        return prev;
      });

      // Queue path: a run is in flight or there's already a queued item
      // ahead of us. Skip the optimistic addMessage — the user message
      // will land in the transcript when the drainer fires start_run
      // and we get run_started with the prompt. Requires a sessionId
      // (the server needs to know which session to queue against);
      // sendMessage's mint-on-first-call only works for direct starts.
      if ((thinking || queuedMessages.length > 0) && sessionId) {
        try {
          await client.enqueueMessage(sessionId, message, {
            triggerRun: true,
            role: "user",
            source: "ink",
          });
        } catch (err) {
          const errorMessage =
            err instanceof Error ? err.message : "Failed to enqueue message";
          actions.setThinking(false, `Error: ${errorMessage}`);
        }
        return;
      }

      // Direct path: optimistic add, then sendMessage (which calls
      // start_run server-side).
      actions.addMessage({
        role: "user",
        content: message,
        timestamp: new Date(),
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
    [
      client,
      connected,
      sendMessage,
      sessionId,
      handleSlashCommand,
      thinking,
      queuedMessages.length,
      escalation,
      actions,
    ],
  );

  // Handle session list input (only when in session_select state).
  // Critical priority: session select is full-screen modal so it must beat
  // everything else when active.
  useKeypress(
    (input, key) => {
      if (debug) {
        const flags = key as unknown as Record<string, unknown>;
        console.error("[DEBUG] useKeypress triggered:", {
          input,
          key: Object.keys(flags).filter((k) => flags[k]),
          uiState,
          isActive: uiState === "session_select",
        });
      }
      if (uiState === "session_select") {
        handleSessionListInput(input, key);
        return true;
      }
      return false;
    },
    {
      isActive: uiState === "session_select",
      priority: KeypressPriority.Critical,
    },
  );

  // Handle Ctrl+C - double tap to quit (like bubbletea).
  // Only active in chat mode to avoid conflicts with session selection.
  // Low priority: this is the global fallback handler that runs after panels
  // (High) and modals (Critical) have had their chance to consume.
  useKeypress(
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
        // Ctrl+N: enter the notifications panel. Same pattern — only
        // toggles on when there's at least one notification to manage.
        if (key.ctrl && input === "n") {
          if (!notificationsPanelFocused && notifications.length > 0) {
            setNotificationsPanelFocused(true);
          } else if (notificationsPanelFocused) {
            setNotificationsPanelFocused(false);
          }
          return;
        }
        // While a panel owns input, swallow other keys here so they
        // don't double-handle (the panel registers its own useInput).
        if (bashPanelFocused || notificationsPanelFocused) {
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
        notificationsPanelFocused,
        notifications.length,
      ],
    ),
    {
      isActive: uiState === "chat" || !!approval || !!mcpApproval,
      priority: KeypressPriority.Low,
    },
  );

  // If every job vanishes (cleared, archived) while the panel is focused,
  // fall back to normal typing — there's nothing to manage.
  useEffect(() => {
    if (bashPanelFocused && bashJobs.length === 0) {
      setBashPanelFocused(false);
    }
  }, [bashPanelFocused, bashJobs.length]);

  // Same fallback for notifications — user dismissed the last one while
  // the panel was focused, drop focus back to the prompt.
  useEffect(() => {
    if (notificationsPanelFocused && notifications.length === 0) {
      setNotificationsPanelFocused(false);
    }
  }, [notificationsPanelFocused, notifications.length]);

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
        {/* Outer chat column. gap={0} so the scroll region butts up
            directly against the input below; the input carries its own
            top border, which provides enough visual separation without
            an extra blank row. Header carries its own internal paddingY
            (in Header.tsx / Logo.tsx) so it still has breathing room
            when visible. */}
        <Box flexDirection="column" gap={0}>
          {/* Header with logo and welcome message - visible only before first message */}
          <Header
            agentName={agentName}
            welcomeText={welcomeText}
            visible={messages.length === 0}
          />

          {/* Message history + dock panels - all scrollable together.
              Goal/Tasks/Workers/QueuedMessages/Notifications/BashJobs/
              EscalationBanner sit after the transcript and scroll with
              history; pin-to-bottom keeps them visible by default.
              No paddingX so panels sit flush with the input below; the
              Scrollable's own paddingRight reserves the scrollbar lane
              and the input's paddingRight (added below) keeps its right
              border aligned with the panels' right border. */}
          <Box flexGrow={1}>
            <ScrollableMessageList
              availableHeight={availableTerminalHeight}
              onApprove={handleApproval}
              onMcpApprove={handleMcpApproval}
            >
              <Goal />
              <Wakeup />
              <Loop />
              <Recap />
              <Tasks />
              <Workers />
              <QueuedMessages />
              <Notifications
                focused={notificationsPanelFocused}
                onUnfocus={() => setNotificationsPanelFocused(false)}
              />
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
              <EscalationBanner />
            </ScrollableMessageList>
          </Box>

          {/* Controls/footer section - measured for dynamic height
              calculation. StatusIndicator (Thinking…) lives inside the
              scrollable region now (just under the transcript), so the
              footer is just input + help. gap={0} — input's bottom
              border provides enough visual break to the help line
              below; help still has paddingBottom for the
              terminal-bottom gap. */}
          <Box ref={controlsRef} flexDirection="column" gap={0}>
            {/* Input prompt - always rendered, disabled only during
                approval prompts or when a side panel owns the keyboard.
                Submission while ``thinking`` is allowed now — handleSubmit
                routes those through enqueue_message so they queue
                instead of racing against the active run.
                paddingRight={1} mirrors the scrollbar lane above so the
                input border's right edge aligns with the panels' right
                border. */}
            <Box paddingRight={1}>
              <MultiLineInputOptimized
                onSubmit={handleSubmit}
                disabled={
                  !connected ||
                  !!approval ||
                  !!mcpApproval ||
                  bashPanelFocused ||
                  notificationsPanelFocused
                }
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
