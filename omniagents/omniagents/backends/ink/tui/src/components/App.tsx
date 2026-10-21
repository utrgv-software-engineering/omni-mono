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
import {
  Box,
  Text,
  useApp,
  useStdout,
  measureElement,
  ResizeObserver,
} from "ink";
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
  useTranscriptDetail,
  useThemeVersion,
  useActions,
  useAppStore,
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
import { SessionComposer, ComposerDrafts } from "./SessionComposer.js";
import { SelectionInputContext } from "./shared/BaseSelectionList.js";
import { ApprovalDialog } from "./ApprovalDialog.js";
import { McpApprovalDialog } from "./McpApprovalDialog.js";
import { Preamble } from "./Preamble.js";
import { SessionManager } from "./SessionManager.js";
import { Scrollable } from "./Scrollable.js";
import { Tasks } from "./Tasks.js";
import { BashJobs } from "./BashJobs.js";
import { GoalPanel } from "./GoalPanel.js";
import { LoopPanel } from "./LoopPanel.js";
import { SkillsPanel } from "./SkillsPanel.js";
import { ThemePanel } from "./ThemePanel.js";
import { QueuedMessages } from "./QueuedMessages.js";
import { ReviewScreen } from "./ReviewScreen.js";
import { AgentsScreen } from "./AgentsScreen.js";
import { FilesScreen } from "./FilesScreen.js";
import { StatusBar, type Screen } from "./StatusBar.js";
import { TopTabs } from "./TopTabs.js";
import { GitPanel } from "./GitPanel.js";
import { ModelPickerPanel } from "./ModelPickerPanel.js";
import { CommandPalettePanel } from "./CommandPalettePanel.js";
import { HelpPanel } from "./HelpPanel.js";
import { ComposerPopup } from "./ComposerPopup.js";
import { buildCommandRows, type CommandRow } from "./command-palette-model.js";
import {
  exportFileName,
  lastAssistantText,
  mcpLines,
  osc52,
  statusLines,
  transcriptToMarkdown,
} from "./session-commands-model.js";
import { writeFileSync } from "node:fs";
import { join as joinPath } from "node:path";
import {
  findSlashCommand,
  parseSlashArgs,
  parseSlashLine,
  unknownCommandMessage,
  slashResultMessage,
} from "../rpc/slash.js";
import {
  nextDismissedToken,
  tokenKey,
  type ComposerCompletion,
  type ComposerToken,
} from "./composer-token.js";
import { ArtifactsPanel } from "./ArtifactsPanel.js";
import { ReviewerPanel } from "./ReviewerPanel.js";
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
import { themedMemo } from "../theme/themed-memo.js";

/**
 * Wrapper to keep ApprovalDialog mounted to prevent flicker
 */
const ApprovalDialogWrapper = themedMemo<{
  approval: ApprovalInfo | null;
  onApprove: (value: "yes" | "always" | "no", requestId?: string) => void;
}>(({ approval, onApprove }) => {
  if (!approval) {
    // Render empty placeholder to keep component mounted
    return null;
  }

  return (
    <ApprovalDialog
      key={approval.call_id}
      tool={approval.tool}
      arguments={approval.arguments}
      metadata={approval.metadata}
      onApprove={(value) => onApprove(value, approval.call_id)}
    />
  );
});

/**
 * Wrapper for the hosted-MCP approval dialog. Mirrors
 * ApprovalDialogWrapper but renders ``McpApprovalDialog`` and matches on
 * ``request_id`` (not call_id).
 */
const McpApprovalDialogWrapper = themedMemo<{
  approval: McpApprovalInfo | null;
  onApprove: (value: "yes" | "no", requestId?: string) => void;
}>(({ approval, onApprove }) => {
  if (!approval) {
    return null;
  }
  return (
    <McpApprovalDialog
      key={approval.request_id}
      tool={approval.tool}
      serverLabel={approval.server_label}
      arguments={approval.arguments}
      onApprove={(value) => onApprove(value, approval.request_id)}
    />
  );
});

/**
 * Wrapper for the elicitation dialog. Question-kind prompts render as
 * the ElicitationBanner (next user submit answers them) instead, so
 * this wrapper only mounts for confirm/select/url/unsupported kinds.
 */
const ElicitationDialogWrapper = themedMemo<{
  elicitation: ElicitationInfo | null;
  onRespond: (answer: ElicitationAnswer, requestId?: string) => void;
}>(({ elicitation, onRespond }) => {
  if (!elicitation || elicitation.kind === "question") {
    return null;
  }
  return (
    <ElicitationDialog
      key={elicitation.elicitation_id}
      elicitation={elicitation}
      onRespond={(answer) => onRespond(answer, elicitation.elicitation_id)}
    />
  );
});

/**
 * Inline "⠋ Thinking…" / status indicator rendered at the bottom of the
 * message transcript (above the dock panels). Reads its own subscriptions
 * so the parent doesn't have to re-render when thinking/status flips, and
 * returns null when there's nothing to show — no phantom row when idle.
 *
 * Hidden while an approval dialog is up: the approval surface itself
 * communicates the paused state.
 */
const InlineStatusIndicator = themedMemo(() => {
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
  onApprove: (value: "yes" | "always" | "no", requestId?: string) => void;
  onMcpApprove: (value: "yes" | "no", requestId?: string) => void;
  onElicit: (answer: ElicitationAnswer, requestId?: string) => void;
}> = ({ availableHeight, onApprove, onMcpApprove, onElicit }) => {
  const messages = useMessages();
  const approval = useApproval();
  const mcpApproval = useMcpApproval();
  const elicitation = useElicitation();
  const preamble = usePreamble();
  const contentVersion = messages.length;
  // ctrl+o re-projects the transcript into a different row set, so the
  // scroll offset it was holding points somewhere unrelated afterwards —
  // pinned at the bottom you landed mid-conversation. There is nothing
  // to preserve, so land at the bottom: the one position that means the
  // same thing in both views.
  const detail = useTranscriptDetail();
  return (
    <Scrollable
      hasFocus={true}
      scrollToBottom={true}
      maxHeight={availableHeight}
      flexGrow={1}
      contentVersion={contentVersion}
      pinToBottomToken={detail ? 1 : 0}
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
  | "approvals"
  | "checks"
  | "help"
  | "theme"
  | "goal"
  | "loop"
  | "skills"
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
  useThemeVersion();

  // Measure controls/footer height dynamically (like gemini-cli)
  const controlsRef = useRef<any>(null);
  const [controlsHeight, setControlsHeight] = useState(0);

  // Exactly one panel drawer may own the keyboard at a time. Every
  // drawer opens from its slash verb (/jobs, /tasks, /goal …); the
  // chords (ctrl+b jobs, ctrl+n notifications, ctrl+g git) are
  // accelerators for the same thing. Opening one closes whatever was
  // open, so panels can never stack two live cursors.
  const [activePanel, setActivePanel] = useState<ActivePanel>(null);
  // Screen router: full-screen scenes switched with alt+1/2. Chat is
  // the conversation (composer, transcript, decisions inline); review
  // is the full-height diff screen. Drawer overlays live on chat.
  const [screen, setScreen] = useState<Screen>("chat");
  // Text queued for the composer (the Files screen's @path mention
  // bridge): consumed by MultiLineInputOptimized on the next render.
  const [pendingInsert, setPendingInsert] = useState<string | null>(null);
  const composerDrafts = React.useRef(new ComposerDrafts()).current;
  // Inline completion popup: the `/command` or `@file` token under the
  // composer cursor, the one-shot completion the popup hands back, and
  // the token the user dismissed with Esc (stays hidden until they move
  // on to a different token).
  const [composerToken, setComposerTokenState] = useState<ComposerToken | null>(
    null,
  );
  const setComposerToken = useCallback((next: ComposerToken | null) => {
    setComposerTokenState((prev) =>
      prev === next ||
      (prev &&
        next &&
        prev.kind === next.kind &&
        prev.query === next.query &&
        prev.row === next.row &&
        prev.start === next.start &&
        prev.end === next.end)
        ? prev
        : next,
    );
  }, []);
  const [composerCompletion, setComposerCompletion] =
    useState<ComposerCompletion | null>(null);
  const [dismissedToken, setDismissedToken] = useState<string | null>(null);
  // The session manager can be entered from chat (ctrl+p, /sessions);
  // only then does Esc have somewhere to go back to.
  const [sessionsReturnable, setSessionsReturnable] = useState(false);
  const promptId =
    approval?.call_id ??
    mcpApproval?.request_id ??
    (elicitation?.kind !== "question"
      ? elicitation?.elicitation_id
      : undefined);
  // Unread stars per tab — "changed since you last looked", the
  // browser/IDE modified-dot. Distinct from ⚠ (needs a decision).
  // Watching a signal while its screen is inactive sets the star;
  // visiting the screen clears it.
  const [unread, setUnread] = useState<Record<Screen, boolean>>({
    chat: false,
    review: false,
    agents: false,
    files: false,
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
  useEffect(() => {
    if (!client || !rpcReady || !sessionId) return;
    let cancelled = false;
    const previous = useAppStore.getState().goal;
    client
      .serverCall("goal.status", {}, sessionId)
      .then((res) => {
        if (cancelled || useAppStore.getState().goal !== previous) return;
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
        // A later reconnect will retry this read.
      });
    return () => {
      cancelled = true;
    };
  }, [client, rpcReady, sessionId, actions]);

  // Seed the wakeup panel on connect / session-switch. Same rationale
  // as the goal seed above — picks up an active schedule that was
  // already running before we attached. wakeup.status may not be
  // registered on every agent; silently ignore so the panel stays empty.
  useEffect(() => {
    if (!client || !rpcReady || !sessionId) return;
    let cancelled = false;
    const previous = useAppStore.getState().wakeup;
    client
      .serverCall("wakeup.status", {}, sessionId)
      .then((res) => {
        if (cancelled || useAppStore.getState().wakeup !== previous) return;
        const snap = (res as { snapshot?: unknown } | undefined)?.snapshot;
        if (snap === null || snap === undefined) {
          actions.setWakeup(null);
        } else if (typeof snap === "object") {
          actions.setWakeup(snap as any);
        }
      })
      .catch(() => {
        // A later reconnect will retry this read.
      });
    return () => {
      cancelled = true;
    };
  }, [client, rpcReady, sessionId, actions]);

  useEffect(() => {
    if (!client || !rpcReady || !sessionId) return;
    let cancelled = false;
    const previous = useAppStore.getState().loops;
    client
      .serverCall("loop.status", {}, sessionId)
      .then((res) => {
        if (cancelled || useAppStore.getState().loops !== previous) return;
        const snap = (res as { snapshot?: unknown } | undefined)?.snapshot;
        if (Array.isArray(snap)) {
          actions.setLoops(snap as any);
        }
      })
      .catch(() => {
        // A later reconnect will retry this read.
      });
    return () => {
      cancelled = true;
    };
  }, [client, rpcReady, sessionId, actions]);

  // Seed the workers panel on connect / session-switch. Same rationale
  // as the goal seed above — picks up workers that were already running
  // before we attached. workers.list may not be registered on every
  // agent; silently ignore so the panel stays empty.
  useEffect(() => {
    if (!client || !rpcReady || !sessionId) return;
    let cancelled = false;
    const previous = useAppStore.getState().subagents;
    client
      .serverCall("workers.list", {}, sessionId)
      .then((res) => {
        if (cancelled || useAppStore.getState().subagents !== previous) return;
        const snap = (res as { snapshot?: unknown } | undefined)?.snapshot;
        if (Array.isArray(snap)) {
          actions.setSubagents(snap as any);
        } else {
          actions.setSubagents([]);
        }
      })
      .catch(() => {
        // A later reconnect will retry this read.
      });
    return () => {
      cancelled = true;
    };
  }, [client, rpcReady, sessionId, actions]);

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

  // The footer's height is whatever it is: the composer grows as you
  // type, the completion popup lists more or fewer rows as you filter,
  // drawers open and close. Observe the element instead of enumerating
  // every state that could change it — a missed dependency here paints
  // the footer over the transcript. Zero is a REAL measurement on
  // screens without a composer (review/agents with no drawer open).
  useLayoutEffect(() => {
    const element = controlsRef.current;
    if (!element) return;
    setControlsHeight(measureElement(element).height);
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setControlsHeight(Math.round(entry.contentRect.height));
      }
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [terminalHeight, screen]);

  // The content region is a flex child that shrinks to whatever the
  // footer leaves, and clips. Its measured height is what the screens
  // get; the arithmetic below is only the first-frame fallback before
  // the observer has reported. Children may hold a stale height for one
  // frame, but the wrapper clips them, so the frame never exceeds the
  // terminal — which is what used to corrupt Ink's redraw.
  const contentRef = useRef<any>(null);
  const [contentHeight, setContentHeight] = useState<number | null>(null);
  useLayoutEffect(() => {
    const element = contentRef.current;
    if (!element) return;
    setContentHeight(measureElement(element).height);
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setContentHeight(Math.round(entry.contentRect.height));
      }
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [terminalHeight, screen, uiState]);
  // A measured region may legitimately be tiny (the key overlay open on
  // a short terminal); forcing a floor above it would make the children
  // overflow their clipped wrapper, which Ink does not paint cleanly.
  const availableTerminalHeight =
    contentHeight !== null
      ? Math.max(1, contentHeight)
      : Math.max(5, terminalHeight - controlsHeight - 2 - 1);

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
    // From chat, "new" means a fresh transcript too; the next send
    // mints the session. At launch there is nothing to clear.
    if (sessionsReturnable) {
      actions.clearMessages();
      actions.setSessionId(undefined);
    }
    setUIState("chat");
  }, [actions, debug, sessionsReturnable]);

  /** Chat → session manager, with a way back. */
  const openSessions = useCallback(() => {
    setActivePanel(null);
    setSessionsReturnable(true);
    setUIState("session_select");
  }, []);

  // Handle approval
  const handleApproval = useCallback(
    (value: "yes" | "always" | "no", requestId?: string) => {
      const approved = value !== "no";
      const alwaysApprove = value === "always";
      approveToolCall(approved, alwaysApprove, requestId ?? approval?.call_id);
    },
    [approveToolCall, approval?.call_id],
  );

  // Hosted-MCP approval — parallel handler. No "always" affordance.
  const handleMcpApproval = useCallback(
    (value: "yes" | "no", requestId?: string) => {
      approveMcpToolCall(value === "yes", requestId ?? mcpApproval?.request_id);
    },
    [approveMcpToolCall, mcpApproval?.request_id],
  );

  // Structured user elicitation — dialog answers (confirm/select/url and
  // typed declines for unsupported kinds) route straight through; the
  // question kind is answered by the submit interceptor below.
  const handleElicitation = useCallback(
    (answer: ElicitationAnswer, requestId?: string) => {
      respondElicitation(answer, requestId ?? elicitation?.elicitation_id);
    },
    [respondElicitation, elicitation?.elicitation_id],
  );

  // Slash commands. The catalog is the server's typeable subset plus the
  // client's own commands; a name outside it is refused, never sent to
  // the model. Arguments follow the command's declared rule.
  const slashCatalog = useRef<{ rows: CommandRow[]; at: number } | null>(null);
  const loadSlashCatalog = useCallback(async (): Promise<CommandRow[]> => {
    const cached = slashCatalog.current;
    if (cached && Date.now() - cached.at < 30_000) return cached.rows;
    const rows = buildCommandRows(
      client ? await client.listSlashCommands() : [],
    );
    slashCatalog.current = { rows, at: Date.now() };
    return rows;
  }, [client]);
  useEffect(() => {
    slashCatalog.current = null;
  }, [client, rpcReady]);

  const handleSlashCommand = useCallback(
    async (cmd: string): Promise<{ result?: string; restore?: boolean }> => {
      const parsed = parseSlashLine(cmd);
      if (!parsed) return {};
      const rows = await loadSlashCatalog();
      const command = findSlashCommand(rows, parsed.name);
      if (!command) {
        return { result: unknownCommandMessage(parsed.name), restore: true };
      }
      const args = parseSlashArgs(command.args, parsed.rest);
      if (!args.ok) {
        return {
          result: `/${command.name}: ${args.error}${command.usage ? ` Usage: /${command.name} ${command.usage}` : ""}`,
          restore: true,
        };
      }
      if (!command.during_run && thinking) {
        return {
          result: `/${command.name} can't run while a response is in progress. Stop it first (esc).`,
          restore: true,
        };
      }

      // A verb with a panel and nothing after it opens the panel; the
      // panel offers the verb's actions as keys.
      if (
        parsed.rest === "" &&
        (command.name === "goal" || command.name === "loop")
      ) {
        setActivePanel(command.name);
        return {};
      }

      // Client-side commands.
      if (command.source === "client") {
        switch (command.name) {
          case "exit":
            exit();
            return {};
          case "model":
            setActivePanel("models");
            return {};
          case "help":
            setActivePanel("commands");
            return {};
          case "keys":
            setActivePanel("help");
            return {};
          case "sessions":
            openSessions();
            return {};
          case "jobs":
            setActivePanel("jobs");
            return {};
          case "tasks":
            setActivePanel("tasks");
            return {};
          case "queue":
            setActivePanel("queue");
            return {};
          case "notifications":
            setActivePanel("notifications");
            return {};
          case "git":
            setActivePanel("git");
            return {};
          case "workers":
            setScreen("agents");
            return {};
          case "artifacts":
            setActivePanel("artifacts");
            return {};
          case "new":
            actions.clearMessages();
            actions.setSessionId(undefined);
            return {};
          case "rename": {
            const title = String(args.args.title ?? "").trim();
            if (!sessionId)
              return {
                result: "Nothing to rename yet — send a message first.",
                restore: true,
              };
            if (!title)
              return { result: "Usage: /rename <title>", restore: true };
            if (!client?.isGranted("update_thread"))
              return {
                result: "This server does not support renaming conversations.",
              };
            try {
              await client.updateThread(sessionId, { title });
              return { result: `Renamed to “${title}”.` };
            } catch (err) {
              return { result: `Rename failed: ${err}` };
            }
          }
          case "fork": {
            if (!sessionId)
              return {
                result: "Nothing to fork yet — send a message first.",
                restore: true,
              };
            if (!client?.isGranted("fork_session"))
              return {
                result: "This server does not support forking conversations.",
              };
            try {
              const res = (await client.forkSession(sessionId)) as {
                new_session_id?: unknown;
              };
              const id =
                typeof res?.new_session_id === "string"
                  ? res.new_session_id
                  : null;
              if (!id)
                return { result: "Fork did not return a new conversation." };
              handleOpenSession(id);
              return {};
            } catch (err) {
              return { result: `Fork failed: ${err}` };
            }
          }
          case "diff":
            setScreen("review");
            return {};
          case "status": {
            const state = useAppStore.getState();
            let workspaceRoot: string | null = null;
            try {
              const res = (await client?.serverCall(
                "fs_get_workspace_root",
                {},
                sessionId || undefined,
              )) as { path?: unknown } | undefined;
              if (typeof res?.path === "string") workspaceRoot = res.path;
            } catch {
              /* no workspace functions on this agent */
            }
            return {
              result: statusLines({
                sessionId,
                model: state.sessionModel,
                usage: state.usage,
                goal: state.goal,
                loops: state.loops,
                workspaceRoot,
                connected,
              }).join("\n"),
            };
          }
          case "mcp": {
            try {
              const res = await client!.mcpListServers(sessionId || undefined);
              return { result: mcpLines(res.servers ?? []).join("\n") };
            } catch (err) {
              return { result: `Could not list MCP servers: ${err}` };
            }
          }
          case "skills":
            setActivePanel("skills");
            return {};
          case "theme":
            setActivePanel("theme");
            return {};
          case "copy": {
            const text = lastAssistantText(messages);
            if (!text) return { result: "Nothing to copy yet." };
            stdout.write(osc52(text));
            return {
              result: `Copied ${text.length} characters to the clipboard.`,
            };
          }
          case "export": {
            const now = new Date();
            const dir = process.env.PWD ?? process.cwd();
            const file = joinPath(dir, exportFileName(sessionId, now));
            try {
              writeFileSync(
                file,
                transcriptToMarkdown(messages, {
                  sessionId,
                  model: useAppStore.getState().sessionModel,
                  exportedAt: now,
                }),
                "utf8",
              );
              return { result: `Exported to ${file}` };
            } catch (err) {
              return { result: `Export failed: ${err}` };
            }
          }
          case "pwd": {
            try {
              const res = (await client!.serverCall(
                "fs_get_workspace_root",
                {},
                sessionId || undefined,
              )) as {
                path?: unknown;
              };
              return {
                result:
                  typeof res?.path === "string" ? res.path : "No workspace.",
              };
            } catch {
              return { result: "No workspace on this agent." };
            }
          }
          case "approvals":
            setActivePanel("approvals");
            return {};
          case "checks":
            setActivePanel("checks");
            return {};
          default:
            return {
              result: unknownCommandMessage(command.name),
              restore: true,
            };
        }
      }

      try {
        // Anchor a session before dispatching: a slash command may be the
        // first thing the user does, and server functions like /goal start
        // runs through the drainer rather than through sendMessage's
        // mint-on-first-call.
        let sid: string | undefined = sessionId || undefined;
        if (!sid) {
          try {
            const ensured = (await client!.serverCall("session.ensure", {})) as
              | { session_id?: unknown }
              | undefined;
            if (typeof ensured?.session_id === "string") {
              sid = ensured.session_id;
              actions.setSessionId(sid);
            }
          } catch {
            /* best-effort — let the call attempt anyway */
          }
        }
        const result = await client!.serverCall(
          command.function ?? command.name,
          args.args,
          sid,
        );
        return { result: slashResultMessage(result) };
      } catch (err) {
        return { result: `Error: ${err}` };
      }
    },
    [
      client,
      sessionId,
      exit,
      actions,
      thinking,
      loadSlashCatalog,
      openSessions,
      handleOpenSession,
      messages,
      stdout,
      connected,
    ],
  );

  // Handle user input
  const handleSubmit = useCallback(
    async (message: string): Promise<boolean | void> => {
      if (!client || !connected) {
        return false;
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
        return await respondElicitation({
          action: "accept",
          value: { text: message },
        });
      }

      // Slash command: handled here or refused here. Never the model's.
      const typed = parseSlashLine(message);
      if (typed) {
        const { result, restore } = await handleSlashCommand(message);
        if (result) {
          actions.addMessage({
            role: "command",
            command: typed.name,
            content: result,
            timestamp: new Date(),
          });
        }
        // A refused command goes back into the composer, as typed.
        return restore ? false : undefined;
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
          const queued = await client.enqueueMessage(sessionId, message, {
            role: "user",
            source: "ink",
          });
          if (!queued.ok)
            throw new Error(queued.reason || "Message was not queued");
        } catch (err) {
          const errorMessage =
            err instanceof Error ? err.message : "Failed to enqueue message";
          if (useAppStore.getState().sessionId === sessionId) {
            actions.setStatus(`Error: ${errorMessage}`, "error", false);
          }
          return false;
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
          composerDrafts.adoptSession(response.session_id);
          if (useAppStore.getState().sessionId === sessionId) {
            actions.markSessionAsMinted(response.session_id);
            actions.setSessionId(response.session_id);
          }
        }
      } catch (err) {
        // Show error in status indicator instead of just logging
        const errorMessage =
          err instanceof Error ? err.message : "Failed to send message";
        if (useAppStore.getState().sessionId === sessionId) {
          actions.setThinking(false, `Error: ${errorMessage}`);
        }
        return false;
      }
    },
    [
      client,
      connected,
      sendMessage,
      composerDrafts,
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
          const order: Screen[] = ["chat", "review", "agents", "files"];
          setScreen((prev) => {
            const index = order.indexOf(prev);
            const step = key.shift ? order.length - 1 : 1;
            return order[(index + step) % order.length]!;
          });
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
        }
        // Panel chords — each toggles its own panel; opening one closes
        // whatever else was open (single-owner rule).
        // Screens are reached with Tab and alt+1..4.
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
        // Ctrl+P: the session manager. "P" as in pick — the same chord
        // editors use for their switcher.
        if (key.ctrl && input === "p") {
          openSessions();
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
        openSessions,
      ],
    ),
    {
      isActive: uiState === "chat" || !!approval || !!mcpApproval,
      priority: KeypressPriority.Low,
    },
  );

  // `?` on an empty composer opens the key reference. High priority so
  // the composer never inserts the character first; the emptiness check
  // is what keeps a real question mark typeable.
  const composerReplyId = escalation
    ? `escalation:${escalation.request_id}`
    : elicitation?.kind === "question"
      ? `question:${elicitation.elicitation_id}`
      : undefined;
  useKeypress(
    useCallback(
      (input, key) => {
        if (input !== "?" || key.ctrl || key.meta) return false;
        const current = composerDrafts.get(sessionId, composerReplyId).current;
        if (current && current.lines.some((line) => line.length > 0))
          return false;
        setActivePanel("help");
        return true;
      },
      [composerDrafts, sessionId, composerReplyId],
    ),
    {
      isActive:
        uiState === "chat" &&
        screen === "chat" &&
        activePanel === null &&
        !promptId,
      priority: KeypressPriority.High,
    },
  );

  // The popup shows for the live token unless the user dismissed that
  // very token; a different token (new position or sigil) reopens it.
  const popupToken =
    screen === "chat" &&
    !promptId &&
    activePanel === null &&
    composerToken &&
    dismissedToken !== tokenKey(composerToken)
      ? composerToken
      : null;
  useEffect(() => {
    setDismissedToken((prev) => nextDismissedToken(prev, composerToken));
  }, [composerToken]);

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
            // still does. Opened from chat, an empty list is a finding.
            onEmpty={sessionsReturnable ? undefined : handleNewSession}
            onExit={sessionsReturnable ? () => setUIState("chat") : undefined}
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
          <TopTabs screen={screen} unread={unread} />

          {/* Message history + dock panels - all scrollable together.
              Goal/Tasks/Workers/QueuedMessages/Notifications/BashJobs/
              EscalationBanner sit after the transcript and scroll with
              history; pin-to-bottom keeps them visible by default.
              No paddingX so panels sit flush with the input below; the
              Scrollable's own paddingRight reserves the scrollbar lane
              and the input's paddingRight (added below) keeps its right
              border aligned with the panels' right border. */}
          <Box
            ref={contentRef}
            flexGrow={1}
            flexShrink={1}
            minHeight={0}
            overflowY="hidden"
          >
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
                onWorkerAction={
                  client && rpcReady && sessionId
                    ? async (worker_id, action) => {
                        const response = (await client.serverCall(
                          action === "resume"
                            ? "workers.resume"
                            : "workers.kill",
                          action === "resume"
                            ? { worker_id, operation_id: crypto.randomUUID() }
                            : { worker_id },
                          sessionId,
                        )) as {
                          ok?: boolean;
                          error?: string;
                          message?: string;
                          snapshot?: import("../store/appStore.js").SubagentSummary[];
                        };
                        if (useAppStore.getState().sessionId !== sessionId)
                          return;
                        if (Array.isArray(response.snapshot)) {
                          actions.setSubagents([
                            ...useAppStore
                              .getState()
                              .subagents.filter(
                                (entry) => entry.kind !== "worker",
                              ),
                            ...response.snapshot,
                          ]);
                        }
                        if (!response.ok)
                          throw new Error(
                            response.message ??
                              response.error ??
                              "Worker action failed",
                          );
                      }
                    : undefined
                }
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
                 first message replaces it with the transcript. When the
                 footer leaves too few rows (a popup or the key overlay is
                 open), draw the logo alone or nothing: content taller
                 than its box is what Ink paints as overlapping rows. */
              <Box
                height={availableTerminalHeight}
                width="100%"
                flexDirection="column"
                justifyContent="center"
                alignItems="center"
              >
                {availableTerminalHeight >= 13 ? (
                  <Header
                    agentName={agentName}
                    welcomeText={welcomeText}
                    visible={true}
                  />
                ) : availableTerminalHeight >= 3 ? (
                  <Logo agentName={agentName} />
                ) : null}
              </Box>
            ) : (
              <SelectionInputContext.Provider value={activePanel === null}>
                <ScrollableMessageList
                  availableHeight={availableTerminalHeight}
                  onApprove={handleApproval}
                  onMcpApprove={handleMcpApproval}
                  onElicit={handleElicitation}
                />
              </SelectionInputContext.Provider>
            )}
          </Box>

          {/* Controls/footer section - measured for dynamic height
              calculation. StatusIndicator (Thinking…) lives inside the
              scrollable region now (just under the transcript), so the
              footer is just input + help. gap={0} — input's bottom
              border provides enough visual break to the help line
              below; help still has paddingBottom for the
              terminal-bottom gap. */}
          <Box ref={controlsRef} flexDirection="column" gap={0} flexShrink={0}>
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
                <SessionComposer
                  drafts={composerDrafts}
                  sessionId={sessionId}
                  replyId={composerReplyId}
                  onSubmit={handleSubmit}
                  disabled={!!promptId || activePanel !== null}
                  history={messageHistory}
                  disableSubmit={!rpcReady}
                  pendingInsert={pendingInsert}
                  onInsertConsumed={() => setPendingInsert(null)}
                  onTokenChange={setComposerToken}
                  completion={composerCompletion}
                  onCompletionConsumed={() => setComposerCompletion(null)}
                />
              </Box>
            )}
            {/* Inline completions for `/command` and `@file`, right under
                the text they complete. Non-modal: typing keeps filtering. */}
            {screen === "chat" && (
              <ComposerPopup
                token={popupToken}
                client={client}
                onComplete={setComposerCompletion}
                onDismiss={() =>
                  setDismissedToken(popupToken ? tokenKey(popupToken) : null)
                }
              />
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
            <GoalPanel
              focused={activePanel === "goal"}
              onUnfocus={() => setActivePanel(null)}
              onAction={async (action) => {
                if (!client) return {};
                return await client.serverCall(
                  `goal.${action}`,
                  {},
                  sessionId || undefined,
                );
              }}
            />
            <LoopPanel
              focused={activePanel === "loop"}
              onUnfocus={() => setActivePanel(null)}
              onStop={async (id) => {
                if (!client) return {};
                return await client.serverCall(
                  "loop.stop",
                  { text: id },
                  sessionId || undefined,
                );
              }}
              onClear={async () => {
                if (!client) return {};
                return await client.serverCall(
                  "loop.clear",
                  {},
                  sessionId || undefined,
                );
              }}
            />
            <SkillsPanel
              focused={activePanel === "skills"}
              onUnfocus={() => setActivePanel(null)}
              client={client}
              sessionId={sessionId || undefined}
            />
            <ThemePanel
              focused={activePanel === "theme"}
              onUnfocus={() => setActivePanel(null)}
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
            <ReviewerPanel
              kind="approvals"
              open={activePanel === "approvals"}
              onClose={() => setActivePanel(null)}
              client={client}
              sessionId={sessionId}
            />
            <ReviewerPanel
              kind="checks"
              open={activePanel === "checks"}
              onClose={() => setActivePanel(null)}
              client={client}
              sessionId={sessionId}
            />
            <HelpPanel
              open={activePanel === "help"}
              screen={screen}
              onClose={() => setActivePanel(null)}
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

            {/* Bottom bar: what's happening (mission tokens + usage). */}
            <StatusBar waitingForSecondCtrlC={waitingForSecondCtrlC} />
          </Box>
        </Box>
      </ScrollProvider>
    </MouseProvider>
  );
};
