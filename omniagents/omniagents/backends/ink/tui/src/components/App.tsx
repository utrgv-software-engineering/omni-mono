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
  useElicitation,
  useSessionId,
  useCurrentRunId,
  usePreamble,
  useBashJobs,
  useRunDiff,
  useSubagents,
  useActions,
} from "../store/appStore.js";
import type {
  ApprovalInfo,
  ElicitationInfo,
  McpApprovalInfo,
} from "../store/appStore.js";
import { Header } from "./Header.js";
import { Logo } from "./Logo.js";
import { MessageListOptimized } from "./MessageListOptimized.js";
import { StatusIndicator } from "./StatusIndicator.js";
import { MultiLineInputOptimized } from "./MultiLineInputOptimized.js";
import { ApprovalDialog } from "./ApprovalDialog.js";
import { McpApprovalDialog } from "./McpApprovalDialog.js";
import { Preamble } from "./Preamble.js";
import { SessionManager } from "./SessionManager.js";
import { Scrollable } from "./Scrollable.js";
import { Tasks } from "./Tasks.js";
import { BashJobs } from "./BashJobs.js";
import { QueuedMessages } from "./QueuedMessages.js";
import { PanelMenu } from "./PanelMenu.js";
import { ReviewScreen } from "./ReviewScreen.js";
import { AgentsScreen } from "./AgentsScreen.js";
import { FilesScreen } from "./FilesScreen.js";
import { StatusBar, type Screen } from "./StatusBar.js";
import { TopTabs } from "./TopTabs.js";
import { GitPanel } from "./GitPanel.js";
import { TerminalScreen } from "./TerminalScreen.js";
import { ModelPickerPanel } from "./ModelPickerPanel.js";
import { CommandPalettePanel } from "./CommandPalettePanel.js";
import { ArtifactsPanel } from "./ArtifactsPanel.js";
import { EscalationBanner } from "./EscalationBanner.js";
import { ElicitationBanner } from "./ElicitationBanner.js";
import {
  ElicitationDialog,
  type ElicitationAnswer,
} from "./ElicitationDialog.js";
import { Notifications } from "./Notifications.js";
import { resolveEscalation } from "../hooks/escalationBridge.js";
import { MouseProvider } from "../contexts/MouseContext.js";
import { ScrollProvider } from "../contexts/ScrollProvider.js";
import { Theme } from "../theme.js";

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
 * Wrapper for the elicitation dialog. Question-kind prompts render as
 * the ElicitationBanner (next user submit answers them) instead, so
 * this wrapper only mounts for confirm/select/url/unsupported kinds.
 */
const ElicitationDialogWrapper = React.memo<{
  elicitation: ElicitationInfo | null;
  onRespond: (answer: ElicitationAnswer) => void;
}>(
  ({ elicitation, onRespond }) => {
    if (!elicitation || elicitation.kind === "question") {
      return null;
    }
    return (
      <ElicitationDialog elicitation={elicitation} onRespond={onRespond} />
    );
  },
  (prev, next) => {
    if (prev.elicitation === next.elicitation) return true;
    if (!prev.elicitation || !next.elicitation) return false;
    return prev.elicitation.elicitation_id === next.elicitation.elicitation_id;
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
 *   2. Preamble — animated streaming agent text, only while streaming
 *   3. InlineStatusIndicator — "⠋ Thinking…" / status line, only when active
 *   4. Approval / MCP approval / elicitation dialog, when present
 *
 * Prose before chrome. The Preamble is the newest agent text — the
 * transcript's continuation — so it reads directly on from the last
 * message instead of having a status line wedged in between. Keeping
 * "Thinking…" below it also holds the spinner still: it stays the last
 * line above the composer rather than drifting up as prose streams in.
 *
 * The dock panels (Tasks/QueuedMessages/Notifications/BashJobs/
 * Escalation) are NOT here — they are fixed chrome in the footer next to
 * the composer, below. This component used to accept them as
 * ``children`` and wrap them in a gap={0} Box; the call site stopped
 * passing any, but the wrapper kept rendering, and an empty flex item
 * still takes the parent's gap on BOTH sides. That silently put 2 blank
 * rows wherever it sat. Removed rather than made conditional: a slot
 * nothing fills is how it got here.
 *
 * Pin-to-bottom keeps the newest content visible by default; scrolling
 * up via mouse wheel or shift+↑/↓ clears the pin (see Scrollable.tsx).
 */
const ScrollableMessageList: React.FC<{
  availableHeight: number;
  onApprove: (value: "yes" | "always" | "no") => void;
  onMcpApprove: (value: "yes" | "no") => void;
  onElicit: (answer: ElicitationAnswer) => void;
}> = ({ availableHeight, onApprove, onMcpApprove, onElicit }) => {
  const messages = useMessages();
  const approval = useApproval();
  const mcpApproval = useMcpApproval();
  const elicitation = useElicitation();
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
          Preamble and approvals — and it is the ONLY thing that spaces
          them. Children must not carry their own marginTop /
          marginBottom or trailing padding, or the rhythm doubles at
          that seam (see TranscriptRowItem's isLast).
          paddingTop separates the first message from the tab bar. */}
      <Box flexDirection="column" gap={1} paddingTop={1}>
        <MessageListOptimized />

        {preamble && <Preamble content={preamble} />}

        <InlineStatusIndicator />

        {approval && (
          <ApprovalDialogWrapper approval={approval} onApprove={onApprove} />
        )}

        {mcpApproval && (
          <McpApprovalDialogWrapper
            approval={mcpApproval}
            onApprove={onMcpApprove}
          />
        )}

        {elicitation && elicitation.kind !== "question" && (
          <ElicitationDialogWrapper
            elicitation={elicitation}
            onRespond={onElicit}
          />
        )}
      </Box>
    </Scrollable>
  );
};

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

/** The single panel drawer that owns the keyboard, or null for the prompt. */
type ActivePanel =
  | "tasks"
  | "workers"
  | "queue"
  | "jobs"
  | "notifications"
  | "git"
  | "models"
  | "commands"
  | "artifacts"
  | null;

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
  const elicitation = useElicitation();
  const sessionId = useSessionId();
  const currentRunId = useCurrentRunId();
  const bashJobs = useBashJobs();
  const actions = useActions();

  // Measure controls/footer height dynamically (like gemini-cli)
  const controlsRef = useRef<any>(null);
  const [controlsHeight, setControlsHeight] = useState(0);

  // Exactly one panel drawer may own the keyboard at a time. Chords
  // toggle their panel (ctrl+b jobs, ctrl+n notifications, ctrl+g git,
  // /model models) and ctrl+a cycles the strip drawers
  // (tasks → workers → queue); opening one closes whatever was open, so
  // panels can never stack two live cursors.
  const [activePanel, setActivePanel] = useState<ActivePanel>(null);
  // Which-key popup (ctrl+a): pick a panel by mnemonic instead of
  // cycling blind. Selecting sets activePanel and closes the menu.
  const [panelMenuOpen, setPanelMenuOpen] = useState(false);
  // Screen router: full-screen scenes switched with alt+1/2. Chat is
  // the conversation (composer, transcript, decisions inline); review
  // is the full-height diff screen. Drawer overlays live on chat.
  const [screen, setScreen] = useState<Screen>("chat");
  // True while the terminal screen's PTY owns the keyboard. Lifted here
  // because the tab bar has to show it: it decides whether Tab cycles
  // tabs or completes a filename, and an invisible mode that reassigns
  // Tab is exactly the trap this design exists to avoid. Arriving on the
  // screen never captures — you press `i`/enter — so leaving always
  // clears it and every arrival starts in the same state.
  const [termCaptured, setTermCaptured] = useState(false);
  useEffect(() => {
    if (screen !== "term") setTermCaptured(false);
  }, [screen]);
  // Text queued for the composer (the Files screen's @path mention
  // bridge): consumed by MultiLineInputOptimized on the next render.
  const [pendingInsert, setPendingInsert] = useState<string | null>(null);

  // Unread stars per tab — "changed since you last looked", the
  // browser/IDE modified-dot. Distinct from ⚠ (needs a decision).
  // Watching a signal while its screen is inactive sets the star;
  // visiting the screen clears it.
  const [unread, setUnread] = useState<Record<Screen, boolean>>({
    chat: false,
    review: false,
    agents: false,
    files: false,
    term: false,
  });
  const runDiffForBadge = useRunDiff();
  const workersForBadge = useSubagents();
  const workersSignature = workersForBadge
    .map((w) => `${w.subagent_id}:${w.status}`)
    .join(",");
  const prevSignals = useRef<{
    messages: number;
    runDiff: unknown;
    subagents: string;
  } | null>(null);
  useEffect(() => {
    const prev = prevSignals.current;
    prevSignals.current = {
      messages: messages.length,
      runDiff: runDiffForBadge,
      subagents: workersSignature,
    };
    if (!prev) return;
    setUnread((current) => {
      let next = current;
      const mark = (target: Screen) => {
        if (screen !== target && !next[target]) {
          next = { ...next, [target]: true };
        }
      };
      if (messages.length > prev.messages) mark("chat");
      if (runDiffForBadge && runDiffForBadge !== prev.runDiff) mark("review");
      if (workersSignature !== prev.subagents) mark("agents");
      return next;
    });
  }, [messages.length, runDiffForBadge, workersSignature, screen]);
  useEffect(() => {
    setUnread((current) =>
      current[screen] ? { ...current, [screen]: false } : current,
    );
  }, [screen]);

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
          actions.setSubagents(snap as any);
        } else {
          actions.setSubagents([]);
        }
      })
      .catch(() => {
        workersSeedRef.current = null;
      });
  }, [client, connected, sessionId, actions]);

  // Reset dock dismissal sets on session change so the freshly bound
  // session starts with the panels showing their full server-side
  // snapshot.
  //
  // Artifacts clear with them. They are session-scoped (the payload
  // carries `session_id`) and are held only in memory — nothing refetches
  // them for a resumed session — so keeping the previous session's
  // artifacts would attribute one conversation's output to another.
  useEffect(() => {
    actions.resetDockDismissals();
    actions.clearArtifacts();
  }, [sessionId, actions]);

  useLayoutEffect(() => {
    if (controlsRef.current) {
      // Zero is a REAL measurement on screens without a composer
      // (review/agents with no drawer open) — skipping it would keep
      // subtracting the stale chat-controls height and strand rows of
      // blank space under those screens.
      setControlsHeight(measureElement(controlsRef.current).height);
    }
    // `screen` changes the controls column contents (the composer only
    // exists on chat), and the drawer region grows/shrinks with
    // activePanel / the menu — all of them must re-measure.
  }, [
    terminalHeight,
    approval,
    mcpApproval,
    thinking,
    screen,
    activePanel,
    panelMenuOpen,
  ]);

  // Calculate available height for messages (gemini-cli approach).
  // The extra -1 is the TopTabs row, which sits outside controlsRef.
  const availableTerminalHeight = Math.max(
    5,
    terminalHeight - controlsHeight - 2 - 1,
  );

  // Ctrl+C handling - require double tap to quit
  const { exit } = useApp();
  const ctrlCCount = useRef(0);
  const ctrlCTimer = useRef<NodeJS.Timeout | null>(null);
  const [waitingForSecondCtrlC, setWaitingForSecondCtrlC] = useState(false);

  // Setup message handlers with batching
  const { approveToolCall, approveMcpToolCall, respondElicitation, cancelRun } =
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

  // Route to the resume screen or straight to chat. The listing itself
  // belongs to SessionManager — it owns which surface to read
  // (list_threads vs list_sessions), its own search state and its own
  // refetch-on-thread_updated subscription, none of which App needs to
  // know about to decide a route.
  useEffect(() => {
    if (!client || !rpcReady) {
      return;
    }
    const next: UIState =
      resumeMode && !initialSessionId ? "session_select" : "chat";
    setUIState(next);
    if (debug) {
      console.error(`[DEBUG] UI state: ${next}`);
    }
  }, [client, rpcReady, resumeMode, initialSessionId, debug]);

  // Reflect connection status into status indicator like bubbletea
  useEffect(() => {
    if (uiState !== "chat") return;
    if (!connected) {
      actions.setStatus("Disconnected from server", "error", false);
    }
  }, [connected, uiState]);

  /** Resume screen → chat, on the conversation the user picked. */
  const handleOpenSession = useCallback(
    (id: string) => {
      if (debug) {
        console.error("[DEBUG] Selected session:", id);
      }
      actions.setSessionId(id);
      setUIState("chat");
    },
    [actions, debug],
  );

  /** Resume screen → chat, with no session (the next send mints one). */
  const handleNewSession = useCallback(() => {
    if (debug) {
      console.error("[DEBUG] Creating new session");
    }
    setUIState("chat");
  }, [debug]);

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

  // Structured user elicitation — dialog answers (confirm/select/url and
  // typed declines for unsupported kinds) route straight through; the
  // question kind is answered by the submit interceptor below.
  const handleElicitation = useCallback(
    (answer: ElicitationAnswer) => {
      respondElicitation(answer);
    },
    [respondElicitation],
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

      // Built-in: /model — open the catalog picker panel. Client-side on
      // purpose: model switching is a stable GUI RPC pair, not a server
      // function.
      if (name === "/model" || name === "/models") {
        setActivePanel("models");
        return { sendToLLM: false };
      }

      // Built-in: /help — the command directory. Client-side because
      // half its rows (this one, /model, /exit) never reach the server,
      // and a directory missing the commands you are reading it in is
      // not a directory.
      if (name === "/help" || name === "/?") {
        setActivePanel("commands");
        return { sendToLLM: false };
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

      // Elicitation interception (question kind): the banner told the
      // user their next message is the answer — send it back as
      // ``elicitation_response(accept, {text})`` instead of starting a
      // new run. Slash commands still pass through, mirroring the
      // escalation opt-out above.
      if (
        elicitation &&
        elicitation.kind === "question" &&
        !message.startsWith("/")
      ) {
        actions.addMessage({
          role: "user",
          content: message,
          timestamp: new Date(),
        });
        await respondElicitation({
          action: "accept",
          value: { text: message },
        });
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
    [
      client,
      connected,
      sendMessage,
      sessionId,
      handleSlashCommand,
      thinking,
      queuedMessages.length,
      escalation,
      elicitation,
      respondElicitation,
      actions,
    ],
  );

  // The resume screen's keyboard belongs to SessionManager: it has four
  // input modes (list / search / rename / confirm) that have to route
  // between themselves, and splitting that across two files is how a
  // key ends up handled twice. It declines chords, so the global Ctrl+C
  // below still wins.

  // Global Ctrl+C — double tap to quit (like bubbletea). Critical and
  // active in EVERY uiState (including connecting/error, where nothing
  // else subscribes): the user must always be able to exit. Panels
  // return false for modified keys, so this always receives the chord.
  useKeypress(
    useCallback(
      (input, key) => {
        if (!(key.ctrl && input === "c")) return false;
        ctrlCCount.current++;

        if (ctrlCTimer.current) {
          clearTimeout(ctrlCTimer.current);
        }

        if (ctrlCCount.current === 1) {
          // First Ctrl+C: cancel the current run / deny a pending
          // approval, then arm the quit window.
          if (thinking && currentRunId) {
            cancelRun(currentRunId);
          } else if (approval) {
            handleApproval("no");
          } else if (mcpApproval) {
            handleMcpApproval("no");
          }
          setWaitingForSecondCtrlC(true);
          ctrlCTimer.current = setTimeout(() => {
            ctrlCCount.current = 0;
            setWaitingForSecondCtrlC(false);
          }, 1000);
        } else if (ctrlCCount.current >= 2) {
          setWaitingForSecondCtrlC(false);
          exit();
        }
        return true;
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
      ],
    ),
    { isActive: true, priority: KeypressPriority.Critical },
  );

  // Chat-mode fallback handler for panel chords and Esc.
  // Low priority: runs after panels (High) and modals (Critical) have
  // had their chance to consume.
  useKeypress(
    useCallback(
      (input, key) => {
        // Tab cycles screens — from anywhere, always. Overlays close so
        // the switch is never invisible. Shift+Tab reverses.
        if (key.tab && !key.ctrl && !key.meta) {
          const order: Screen[] = ["chat", "review", "agents", "files", "term"];
          setScreen((prev) => {
            const index = order.indexOf(prev);
            const step = key.shift ? order.length - 1 : 1;
            return order[(index + step) % order.length]!;
          });
          setPanelMenuOpen(false);
          return;
        }
        // Alt+digit jumps stay as direct accelerators.
        if (key.meta && !key.ctrl) {
          if (input === "1") {
            setScreen("chat");
            return;
          }
          if (input === "2") {
            setScreen("review");
            return;
          }
          if (input === "3") {
            setScreen("agents");
            return;
          }
          if (input === "4") {
            setScreen("files");
            return;
          }
          if (input === "5") {
            setScreen("term");
            return;
          }
        }
        // Panel chords — each toggles its own panel; opening one closes
        // whatever else was open (single-owner rule).
        // Ctrl+] is the raw GS byte (0x1d); Ink only sets key.ctrl for
        // the letter range.
        if ((key.ctrl && input === "]") || input === "\u001d") {
          setScreen((prev) => (prev === "term" ? "chat" : "term"));
          return;
        }
        if (key.ctrl && input === "b") {
          setActivePanel((prev) => (prev === "jobs" ? null : "jobs"));
          return;
        }
        if (key.ctrl && input === "n") {
          setActivePanel((prev) =>
            prev === "notifications" ? null : "notifications",
          );
          return;
        }
        if (key.ctrl && input === "g") {
          setActivePanel((prev) => (prev === "git" ? null : "git"));
          return;
        }
        // Ctrl+K: the slash-command directory. The conventional palette
        // chord, and free here — the composer binds only Ctrl+D.
        if (key.ctrl && input === "k") {
          setActivePanel((prev) => (prev === "commands" ? null : "commands"));
          return;
        }
        // Ctrl+A: toggle the which-key panel menu — one mnemonic
        // keypress to any panel, with live summaries.
        if (key.ctrl && input === "a") {
          setPanelMenuOpen((prev) => !prev);
          return;
        }
        // Ctrl+O: toggle the transcript between semantic activity beats
        // and the mechanical per-tool view.
        if (key.ctrl && input === "o") {
          actions.toggleTranscriptDetail();
          return;
        }
        // Esc closes the display-only drawers (tasks/workers).
        // Interactive panels — queue included, now that it owns a cursor
        // and a cancel key — handle their own Esc at High priority.
        if (
          key.escape &&
          (activePanel === "tasks" || activePanel === "workers")
        ) {
          setActivePanel(null);
          return;
        }
        // While a panel owns input, swallow other keys here so they
        // don't double-handle (the panel registers its own useInput).
        if (activePanel !== null) {
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
      },
      [
        thinking,
        currentRunId,
        approval,
        mcpApproval,
        cancelRun,
        handleApproval,
        handleMcpApproval,
        activePanel,
        actions,
      ],
    ),
    {
      isActive: uiState === "chat" || !!approval || !!mcpApproval,
      priority: KeypressPriority.Low,
    },
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
        <Text color={Theme.textChrome}>Connecting to {agentName}...</Text>
      </Box>
    );
  }

  if (uiState === "error" || (error && !connected)) {
    return (
      <Box flexDirection="column">
        <Text color={Theme.stateFailed}>Failed to connect to server:</Text>
        <Text color={Theme.textChrome}>
          {error?.message || "Unknown error"}
        </Text>
      </Box>
    );
  }

  if (uiState === "session_select") {
    return (
      <Box flexDirection="column">
        <Box paddingX={1}>
          <Logo agentName={agentName} />
        </Box>
        <Box marginTop={1}>
          <SessionManager
            client={client}
            onOpen={handleOpenSession}
            onNew={handleNewSession}
            // Nothing to resume is not a screen worth showing: `--resume`
            // on a fresh install used to land straight in chat, and it
            // still does.
            onEmpty={handleNewSession}
          />
        </Box>
      </Box>
    );
  }

  // Chat UI (uiState === 'chat')
  return (
    <MouseProvider mouseEventsEnabled={!!enableMouse}>
      <ScrollProvider>
        {/* Outer chat column, pinned to the terminal height so the app
            always fills the screen: tabs at the top, content stretching
            between, composer/status at the bottom — regardless of how
            short the transcript is. (-1 avoids ink's trailing-newline
            scroll jitter.) gap={0}: the input carries its own top
            border for separation. */}
        <Box flexDirection="column" gap={0} height={terminalHeight - 1}>
          {/* Header with logo and welcome message - visible only before first message */}
          {/* Browser-style tabs on the top row: where you are. */}
          <TopTabs
            screen={screen}
            unread={unread}
            termCaptured={termCaptured}
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
            {screen === "review" ? (
              <ReviewScreen
                active={screen === "review"}
                height={availableTerminalHeight}
                client={client}
                onExit={() => setScreen("chat")}
              />
            ) : screen === "agents" ? (
              <AgentsScreen
                active={screen === "agents"}
                height={availableTerminalHeight}
                onExit={() => setScreen("chat")}
              />
            ) : screen === "term" ? (
              <TerminalScreen
                active={screen === "term"}
                height={availableTerminalHeight}
                client={client}
                sessionId={sessionId ?? null}
                captured={termCaptured}
                onCapturedChange={setTermCaptured}
                onExit={() => setScreen("chat")}
              />
            ) : screen === "files" ? (
              <FilesScreen
                active={screen === "files"}
                height={availableTerminalHeight}
                client={client}
                onExit={() => setScreen("chat")}
                onMention={(path) => {
                  setPendingInsert(`@${path} `);
                  setScreen("chat");
                }}
              />
            ) : messages.length === 0 ? (
              /* Fresh session: the dashboard IS the body, vertically
                 centered in the full content region, LazyVim-style. The
                 first message replaces it with the transcript. */
              <Box
                height={availableTerminalHeight}
                width="100%"
                flexDirection="column"
                justifyContent="center"
                alignItems="center"
              >
                <Header
                  agentName={agentName}
                  welcomeText={welcomeText}
                  visible={true}
                />
              </Box>
            ) : (
              <ScrollableMessageList
                availableHeight={availableTerminalHeight}
                onApprove={handleApproval}
                onMcpApprove={handleMcpApproval}
                onElicit={handleElicitation}
              />
            )}
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
            {/* Composer only exists on the chat screen; review is
                keyboard-driven and reclaims the rows. */}
            {screen === "chat" && (
              <Box paddingRight={1}>
                <MultiLineInputOptimized
                  onSubmit={handleSubmit}
                  disabled={
                    !connected ||
                    !!approval ||
                    !!mcpApproval ||
                    activePanel !== null ||
                    panelMenuOpen
                  }
                  history={messageHistory}
                  pendingInsert={pendingInsert}
                  onInsertConsumed={() => setPendingInsert(null)}
                />
              </Box>
            )}

            {/* Blocking prompts render BELOW the input — they are the
                thing the next submit answers, so they sit at the point
                of composition instead of scrolling away with history.
                Rendered on every screen so a decision is never hidden. */}
            <EscalationBanner />
            <ElicitationBanner />

            {/* IDE bottom-panel region: drawers are FIXED chrome between
                content and the status bar — like VS Code's terminal
                panel. They exist on every screen and never scroll with
                history. The measured controls height grows when one
                opens, so the content region shrinks to make room. */}
            <Tasks focused={activePanel === "tasks"} />
            <QueuedMessages
              focused={activePanel === "queue"}
              onUnfocus={() => setActivePanel(null)}
              onCancel={async (itemId) => {
                if (!client || !sessionId) {
                  return { ok: false, reason: "no session" };
                }
                return await client.cancelQueuedMessage(sessionId, itemId);
              }}
            />
            <Notifications
              focused={activePanel === "notifications"}
              onUnfocus={() => setActivePanel(null)}
            />
            <BashJobs
              focused={activePanel === "jobs"}
              onUnfocus={() => setActivePanel(null)}
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
            <ArtifactsPanel
              focused={activePanel === "artifacts"}
              onUnfocus={() => setActivePanel(null)}
            />
            <GitPanel
              focused={activePanel === "git"}
              onUnfocus={() => setActivePanel(null)}
              client={client}
            />
            <ModelPickerPanel
              open={activePanel === "models"}
              onClose={() => setActivePanel(null)}
              client={client}
              sessionId={sessionId}
            />
            <CommandPalettePanel
              open={activePanel === "commands"}
              onClose={() => setActivePanel(null)}
              client={client}
              // Completes into the composer rather than running: a
              // command may still need arguments, and the user has to
              // be able to read one before committing to it.
              onComplete={(text) => setPendingInsert(text)}
            />

            {/* Which-key panel picker — works from any screen. Workers
                graduated to the agents screen; its row routes there. */}
            <PanelMenu
              open={panelMenuOpen}
              onSelect={(panel) => {
                if (panel === "workers") {
                  setScreen("agents");
                } else if (panel === "terminal") {
                  setScreen("term");
                } else {
                  setActivePanel(panel);
                }
                setPanelMenuOpen(false);
              }}
              onClose={() => setPanelMenuOpen(false)}
            />

            {/* Bottom bar: what's happening (mission tokens + usage). */}
            <StatusBar waitingForSecondCtrlC={waitingForSecondCtrlC} />
          </Box>
        </Box>
      </ScrollProvider>
    </MouseProvider>
  );
};
