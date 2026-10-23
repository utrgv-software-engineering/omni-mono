import { useStore } from '@nanostores/react';
import type { PendingMessage } from '@omniagents/chat-ui/ChatShell';
import { type ArtifactItem, ArtifactsPanel } from '@omniagents/chat-ui/components/ArtifactsPanel';
import { ConversationComposer } from '@omniagents/chat-ui/components/ConversationComposer';
import { ElicitationCard } from '@omniagents/chat-ui/components/ElicitationCard';
import { EscalationBanner } from '@omniagents/chat-ui/components/EscalationBanner';
import { GoalPanel } from '@omniagents/chat-ui/components/GoalPanel';
import { Header } from '@omniagents/chat-ui/components/Header';
import { LoopPanel } from '@omniagents/chat-ui/components/LoopPanel';
import { ArtifactPortalProvider, MessageList } from '@omniagents/chat-ui/components/MessageList';
import { ModelSessionControls } from '@omniagents/chat-ui/components/ModelSessionControls';
import { Notifications } from '@omniagents/chat-ui/components/Notifications';
import { PillStrip } from '@omniagents/chat-ui/components/PillStrip';
import { QueuedMessages } from '@omniagents/chat-ui/components/QueuedMessages';
import { RecapPanel } from '@omniagents/chat-ui/components/RecapPanel';
import { SessionList } from '@omniagents/chat-ui/components/SessionList';
import { Sidebar } from '@omniagents/chat-ui/components/Sidebar';
import { VoiceDock } from '@omniagents/chat-ui/components/VoiceDock';
import { WakeupPanel } from '@omniagents/chat-ui/components/WakeupPanel';
import { OmniAgentsHeaderActionsPortal, OmniAgentsHeaderActionsProvider } from '@omniagents/chat-ui/header-actions';
import { useAutoRetry } from '@omniagents/chat-ui/hooks/use-auto-retry';
import { useChatBoot } from '@omniagents/chat-ui/hooks/use-chat-boot';
import { useChatSession, useSessionField } from '@omniagents/chat-ui/hooks/use-chat-session';
import { SESSION_LIST_LIMIT, useConversationManagement } from '@omniagents/chat-ui/hooks/use-conversation-management';
import { useRealtimeVoice } from '@omniagents/chat-ui/hooks/use-realtime-voice';
import { useChatHost } from '@omniagents/chat-ui/host';
import { useRPCClient, useRPCConnected } from '@omniagents/chat-ui/rpc-context';
import { clearColumnActivity, publishColumnActivity } from '@omniagents/chat-ui/services/column-activity';
import { VoiceScopeContext } from '@omniagents/chat-ui/services/voice-recording';
import {
  createCursorAssigner,
  fullEntry,
  lastEntrySignal,
  type SessionController,
  transcriptPage,
} from '@omniagents/chat-ui/session-controller';
import { useUiConfig } from '@omniagents/chat-ui/ui-config';
import { voiceActive } from '@omniagents/client/machines/voice-session.machine';
import { respondToApproval } from '@omniagents/client/rpc/approval-response';
import { loadCanonicalSessionList } from '@omniagents/client/rpc/canonical-session-list';
import type { ElicitationRequest, ElicitationResponse } from '@omniagents/client/rpc/elicitation';
import type { ExecutionTarget } from '@omniagents/client/rpc/execution';
import { parsePlanResult } from '@omniagents/client/rpc/plans-and-diffs';
import { getConversationDraft, updateConversationDraft } from '@omniagents/client/session/conversation-drafts';
import { conversationIsReady } from '@omniagents/client/session/conversation-readiness';
import {
  $activityBySession,
  type BashJobsKillResult,
  type BashJobsTailResult,
  type BashJobSummary,
  mergeWorkersSnapshot,
  normalizeSubagentSnapshot,
  publishBashJobs,
  registerActivityActions,
  type WorkersKillResult,
} from '@omniagents/client/transcript/activity-store';
import {
  negotiatedPlanTasks,
  rpcPlanTasks,
  type TaskSummary,
} from '@omniagents/client/transcript/canonical-plan-tasks';
import type { RunDiffItem } from '@omniagents/client/transcript/chat-types';
import { createVoiceMergeLedger, mergeVoiceTranscript } from '@omniagents/client/transcript/merge-voice-transcript';
import { publishRunDiff } from '@omniagents/client/transcript/run-diff-store';
import { uuidv4 } from '@omniagents/client/util/uuid';
import { Button } from '@omniagents/ui/button';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@omniagents/ui/resizable';
import { Spinner } from '@omniagents/ui/spinner';
import { useSelector } from '@xstate/react';
import { AnimatePresence, motion } from 'framer-motion';
import React, { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { waitFor } from 'xstate';

type UIState = 'connecting' | 'resume' | 'chat' | 'error';

export type ClientToolCallHandler = (
  toolName: string,
  args: Record<string, unknown>
) => Promise<{ ok: boolean; result?: Record<string, unknown>; error?: Record<string, unknown> }>;

export type AppProps = {
  sessionId?: string;
  /** Explicit execution identity. Never inferred from the conversation id. */
  executionTarget?: ExecutionTarget;
  onSessionChange?: (sessionId: string | undefined) => void;
  variables?: Record<string, unknown>;
  voiceVariables?: Record<string, unknown>;
  greeting?: string;
  /** One-tap example tasks shown on the empty conversation. */
  suggestions?: ReadonlyArray<{ label: string; prompt: string }>;
  onReady?: () => void;
  headerActionsTargetId?: string;
  headerActionsCompact?: boolean;
  pendingMessages?: PendingMessage[];
  /** Called once this chat claims the pre-launch intent queue. */
  onPendingMessagesFlushed?: () => void;
  /** Extra composer chips (e.g. attach-project) forwarded to the Input row. */
  composerExtras?: React.ReactNode;
  onClientToolCall?: ClientToolCallHandler;
  onController?: (controller: SessionController | null) => void;
  onRunEnd?: (info: { runId?: string; reason?: string }) => void;
  onRunStarted?: (runId: string) => void;
  pendingPlan?: import('@omniagents/client/transcript/chat-types').PlanItem | null;
  onPlanDecision?: (approved: boolean) => void;
  /** Routine (scheduled task) id when this column hosts a routine run. */
  routineId?: string;
  workspaceDir?: string;
  /** Opens a column app (deck sidecar tab). When absent — hosts without a
   *  deck column — pills that would deep-link fall back to a popover. */
  onOpenApp?: (appId: string) => void;
  /** Transcript-viewer mode: no composer or pill row — used when this app
   *  is embedded to READ another session (e.g. a subagent's transcript in
   *  the Agents sidecar detail page). */
  readOnly?: boolean;
  /** Refetch a read-only transcript when its external worker advances. */
  transcriptRevision?: string;
};

export function App(props: AppProps) {
  const config = useUiConfig();
  const client = useRPCClient();
  const connectionKey = useMemo(() => uuidv4(), [client]);
  const [selection, setSelection] = useState(() => ({
    propId: props.sessionId,
    id: props.sessionId ?? config.session ?? uuidv4(),
    resume: !props.sessionId && config.searchParams.get('resume') === 'true',
  }));
  const [initialIntent] = useState(() => ({ sessionId: selection.id, text: config.searchParams.get('initial') }));
  if (selection.propId !== props.sessionId) {
    setSelection({ propId: props.sessionId, id: props.sessionId ?? uuidv4(), resume: false });
  }
  const select = useCallback(
    (requested?: string) => {
      const id = requested ?? uuidv4();
      setSelection({ propId: props.sessionId, id, resume: false });
      props.onSessionChange?.(id);
    },
    [props.sessionId, props.onSessionChange]
  );
  return (
    <SessionView
      {...props}
      key={`${connectionKey}:${selection.id}`}
      sessionId={selection.id}
      onSelectConversation={select}
      resumeRequested={selection.resume}
      initialMessage={selection.id === initialIntent.sessionId ? initialIntent.text : null}
    />
  );
}

/** This view has immutable session identity. UI layout may unmount; its
 * connection-owned controller and in-flight operations do not. */
function SessionView({
  sessionId: sessionIdProp,
  executionTarget,
  onSessionChange,
  variables: variablesProp,
  voiceVariables,
  greeting,
  suggestions,
  onReady,
  headerActionsTargetId,
  headerActionsCompact,
  pendingMessages,
  onPendingMessagesFlushed,
  composerExtras,
  onClientToolCall,
  onController,
  onRunEnd,
  onRunStarted,
  pendingPlan,
  onPlanDecision,
  routineId,
  workspaceDir,
  onOpenApp,
  readOnly,
  transcriptRevision,
  onSelectConversation,
  resumeRequested,
  initialMessage,
}: AppProps & {
  sessionId: string;
  onSelectConversation: (id?: string) => void;
  resumeRequested: boolean;
  initialMessage: string | null;
}) {
  const environmentId = executionTarget?.environmentId;
  const uiConfig = useUiConfig();
  const host = useChatHost();
  const [ui, setUI] = useState<UIState>('connecting');
  const client = useRPCClient();
  const machine = useChatSession(client, sessionIdProp);
  const session = machine.controller;
  const [modelMutating] = useSessionField(session, 'modelMutating');
  // Stable refs so the run_started/run_end subscriptions (set up once) always
  // call the latest callbacks without re-subscribing.
  const onRunEndRef = useRef(onRunEnd);
  onRunEndRef.current = onRunEnd;
  const onRunStartedRef = useRef(onRunStarted);
  onRunStartedRef.current = onRunStarted;
  const connected = useRPCConnected();
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [agentName, setAgentName] = useState<string>('OmniAgent');
  const [welcomeText, setWelcomeText] = useState<string | undefined>(undefined);

  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [artifactsPanelOpen, setArtifactsPanelOpen] = useState(false);
  const [artifactsPanelWidth, setArtifactsPanelWidth] = useState(() => {
    try {
      const stored = localStorage.getItem('artifacts-panel-width');
      return stored ? parseInt(stored, 10) : 240;
    } catch {
      return 240;
    }
  });
  const [isLargeScreen, setIsLargeScreen] = useState(() => window.innerWidth >= 1024);
  const [minimalMode] = useState(() => uiConfig.minimal);
  const [workspaceSupported, setWorkspaceSupported] = useState(false);
  // Seed from the workspaceDir prop the launcher passes down for project-scoped
  // surfaces (Code tab). The chat-boot RPC still runs after connect to confirm
  // and refresh — but seeding keeps the folder chip stable while the
  // round-trip is in flight.
  const [workspacePath, setWorkspacePath] = useSessionField(session, 'workspacePath');
  // Keep workspacePath aligned with the prop when the launcher swaps the
  // project under us (e.g. moving a tab to a different project). The chat-boot
  // / session-restore RPCs will overwrite it again once they resolve; this
  // just keeps the visual in sync until then.
  useEffect(() => {
    if (workspaceDir) {
      setWorkspacePath(workspaceDir);
    }
  }, [workspaceDir]);
  // Background-bash live override: `ui.bash_jobs.update` broadcasts (and the
  // kill/tail/list server calls below) push a fresh snapshot here. When non-
  // null this takes precedence over the snapshot derived from tool_result
  // metadata in `items`. Reset to null on session change so the new session
  // starts from its own history-derived state.
  const [liveBashJobs, setLiveBashJobs] = useSessionField(session, 'liveBashJobs');
  const [goalSnapshot, setGoalSnapshot] = useSessionField(session, 'goalSnapshot');
  const [wakeupSnapshot, setWakeupSnapshot] = useSessionField(session, 'wakeupSnapshot');
  const [loopTasks, setLoopTasks] = useSessionField(session, 'loopTasks');
  // Dismissed IDs for each docked panel. Snapshotted on user submit:
  // every item currently in a terminal state gets added so it disappears
  // from the panel when the next run begins. Items spawned during the
  // new run aren't in the set yet, so they show normally; when THEY
  // exit they remain visible until the user's next submit. Reset on
  // session change so a fresh session starts clean.
  const [dismissedWorkerIds, setDismissedWorkerIds] = useSessionField(session, 'dismissedWorkerIds');
  const [dismissedJobIds, setDismissedJobIds] = useSessionField(session, 'dismissedJobIds');
  const [dismissedTaskIds, setDismissedTaskIds] = useSessionField(session, 'dismissedTaskIds');
  // Notifications accumulate from the agent's `notify` builtin calls;
  // dismissed manually via the docked panel buttons.
  const [notifications, setNotifications] = useSessionField(session, 'notifications');
  // Most recent session recap (from /recap or a programmatic trigger).
  // Single-slot — a new recap replaces the old; dismissible.
  const [recap, setRecap] = useSessionField(session, 'recap');
  // Pending agent escalation — the next user submit becomes the reply.
  const [escalation] = useSessionField(session, 'escalation');
  // Element backing the maximized-artifact portal. Callback ref triggers a
  // re-render when the chat-column wrapper attaches/detaches.
  const [chatColumnEl, setChatColumnEl] = useState<HTMLDivElement | null>(null);
  const [queuedMessages, setQueuedMessages] = useSessionField(session, 'queuedMessages');
  const [speakRepliesEnabled, setSpeakRepliesEnabled] = useState(false);
  const readyRef = useRef(false);
  const voiceRunRef = useRef(false);
  const onSessionChangeRef = useRef(onSessionChange);
  useEffect(() => {
    onSessionChangeRef.current = onSessionChange;
  }, [onSessionChange]);
  useEffect(() => {
    readyRef.current = false;
  }, [uiConfig.runtimeBaseUrl]);

  // Chat session state machine — manages items, sessionId, runId, thinking,
  // status, tool status, and approval state.
  const runActive = machine.thinking;
  const {
    actor,
    items,
    thinking,
    status,
    statusSpinner,
    statusItalic,
    toolStatus,
    runId,
    sessionId,
    stagedContext,
    loadSession,
    approvalDecided,
    stageContext,
  } = machine;

  useEffect(() => {
    if (readOnly && connected && ui === 'chat' && transcriptRevision) {
      void loadSession(sessionIdProp, { force: true }).catch(() => undefined);
    }
  }, [readOnly, connected, ui, transcriptRevision, loadSession, sessionIdProp]);

  // Hosted realtime voice — one session per chat surface. The dock renders
  // above the composer; live transcript items merge into MessageList below.
  const voice = useRealtimeVoice();
  const voiceItems = useSelector(voice.actor, (s) => s.context.items);
  const voiceIsActive = useSelector(voice.actor, voiceActive);
  const voiceSessionId = useSelector(voice.actor, (s) => s.context.sessionId);
  const voiceMuted = useSelector(voice.actor, (s) => s.context.muted);

  // Voice-first: adopt the server-minted session id so the chat surface and
  // the voice session stay one thread (tools, approvals, persistence).
  useEffect(() => {
    if (!voiceIsActive || !voiceSessionId) {
      return;
    }
    if (!actor.getSnapshot().context.sessionId) {
      onSelectConversation(voiceSessionId);
    }
    // A voice turn's tool approvals broadcast over THIS /ws channel, and
    // the server only fans out to channels the session knows about. A
    // session loaded from the list is already registered (loadSession →
    // resume_session), but a freshly minted one is not — without this the
    // approval is queued server-side and the voice turn hangs with no card.
    void client.registerSession(voiceSessionId, true).catch(() => {});
  }, [voiceIsActive, voiceSessionId, actor, onSelectConversation, client]);

  const voiceWasActiveRef = useRef(false);

  // One transcript from two streams that share no clock — see
  // merge-voice-transcript.ts. The ledger is per-surface state, so it lives
  // in a ref rather than in either machine.
  const mergeLedgerRef = useRef(createVoiceMergeLedger());
  const mergedItems = useMemo(
    () => mergeVoiceTranscript(items, voiceItems, mergeLedgerRef.current, voiceIsActive),
    [items, voiceItems, voiceIsActive]
  );

  // Unified subagent list (workers + agent-tool runs). The session-keyed
  // activity store is the single source of truth — this component publishes
  // into it (broadcasts, seeds, kill responses) and reads its own session's
  // slice back for the pill row, the same data the Agents sidecar renders.
  const activityBySession = useStore($activityBySession, { keys: sessionId ? [sessionId] : [] });
  const subagents = useMemo(
    () => (sessionId ? (activityBySession[sessionId]?.subagents ?? []) : []),
    [activityBySession, sessionId]
  );

  // Boot orchestrator — composes server → RPC → bootstrap → session load into
  // a single state machine with automatic teardown on disconnect. Replaces the
  // imperative mount-effect chain that used to live here.
  const initialBootSessionId = sessionIdProp || uiConfig.searchParams.get('session') || undefined;
  const bootState = useChatBoot({
    client,
    chatSession: machine,
    sessionId: initialBootSessionId,
    executionTarget,
    // A read-only transcript viewer needs no voice channel — and every
    // avoided socket matters when several viewers share the sandbox origin.
    wsRealtimeUrl: readOnly ? undefined : uiConfig.wsRealtimeUrl,
    token: uiConfig.token,
  });
  // A dropped runtime or a server restart is recovered quietly: the
  // transcript stays on screen, the composer waits, and the boot machine is
  // retried on a capped backoff. Only a long, continuous failure earns the
  // full-column card — and retries keep running behind it.
  const bootFailed =
    bootState.phase === 'connectionError' || bootState.phase === 'bootstrapError' || bootState.phase === 'sessionError';
  const bootRetry = useAutoRetry({ failing: bootFailed, healthy: bootState.ready, retry: bootState.retry });
  // A permanent transport failure (protocol mismatch, bad credentials) will
  // not fix itself in a minute; show the card at once, retries keep going.
  const transportPermanent = useSelector(client.actor, (s) => Boolean(s.context.permanent));
  const bootCardDue = bootRetry.exhausted || (bootFailed && transportPermanent);
  const conversationReady = conversationIsReady({
    connected,
    bootReady: bootState.ready,
    sessionReady: machine.phase !== 'initializing' && machine.phase !== 'initError' && !modelMutating,
    sessionId,
    expectedSessionId: sessionIdProp,
  });

  const {
    sessions,
    refreshSessions,
    managementSupported,
    setSearchQuery,
    searchResults,
    searching: conversationsSearching,
    busyThreadIds,
    operationError: conversationOperationError,
    dismissOperationError: dismissConversationOperationError,
    renameThread,
    setThreadPinned,
    archiveThread,
    restoreThread,
  } = useConversationManagement(client, connected && !readOnly);

  const [elicitations, setElicitations] = useState<ElicitationRequest[]>([]);
  useEffect(() => {
    const refresh = () => {
      setElicitations(
        client.elicitations
          .list()
          .filter((request) => request.sessionId === undefined || request.sessionId === sessionId)
      );
    };
    refresh();
    return client.elicitations.onChange(refresh);
  }, [client, sessionId]);
  const respondToElicitation = useCallback(
    (request: ElicitationRequest, response: ElicitationResponse) =>
      client.elicitations.respond(request.elicitationId, response),
    [client]
  );

  // Publish this column's live activity (thinking / tool line / pending
  // approval) so deck chrome can show a glanceable "now doing X" without
  // reaching into the transcript. Scoped by the same context the voice
  // system uses (the Code tab id).
  const activityScope = useContext(VoiceScopeContext);
  const pendingApproval = useMemo(
    () => items.some((it) => (it as { type?: string }).type === 'approval') || elicitations.length > 0,
    [items, elicitations.length]
  );
  useEffect(() => {
    if (!activityScope) {
      return;
    }
    publishColumnActivity(activityScope, {
      thinking: !!thinking,
      text: toolStatus || status || null,
      pendingApproval,
    });
  }, [activityScope, thinking, toolStatus, status, pendingApproval]);
  useEffect(() => {
    if (!activityScope) {
      return;
    }
    return () => clearColumnActivity(activityScope);
  }, [activityScope]);

  // Sync capabilities from the boot machine into local state. The boot
  // machine is the source of truth; these local useStates exist because
  // downstream components consume them as plain values and some (like
  // workspacePath) are also updated by session selection post-boot.
  useEffect(() => {
    const caps = bootState.capabilities;
    if (!caps) {
      return;
    }
    setAgentName(caps.agentName);
    setWelcomeText(caps.welcomeText);
    setVoiceEnabled(caps.voiceEnabled);
    setWorkspaceSupported(caps.workspaceSupported);
    if (caps.workspacePath) {
      setWorkspacePath((previous) => previous ?? caps.workspacePath ?? null);
    }
    // Note: the host window title (index.html "Omni Code") is left alone —
    // overwriting it with the agent name made the title flip between tabs.
  }, [bootState.capabilities]);

  // React to boot phase → drive the top-level UI mode. In resume mode
  // (user explicitly asked to pick a session), show the session list
  // once bootstrap is done. Otherwise, show chat as soon as boot is
  // ready.
  useEffect(() => {
    if (bootCardDue) {
      setUI('error');
      return;
    }
    if (bootFailed || !bootState.ready) {
      // Within the quiet retry window the current view stays as it is.
      return;
    }
    if (resumeRequested) {
      void refreshSessions();
      setUI('resume');
    } else {
      setUI('chat');
    }
  }, [bootFailed, bootCardDue, bootState.ready, refreshSessions, sessionIdProp, uiConfig.searchParams]);

  // View/host notifications only. Session data and client-request replies
  // are owned by the controller even when no view is mounted.
  useEffect(() => {
    const offs = [
      session.on('run_started', (p: any) => {
        if (typeof p?.run_id === 'string') {
          onRunStartedRef.current?.(p.run_id);
        }
        onSessionChangeRef.current?.(session.id);
        void refreshSessions();
      }),
      session.on('run_end', (p: any) => {
        onRunEndRef.current?.({ runId: p?.run_id, reason: String(p?.end_reason ?? 'completed') });
        void refreshSessions();
      }),
    ];
    return () => offs.forEach((off) => off());
  }, [session, refreshSessions]);
  useEffect(() => {
    if (!onClientToolCall || readOnly) {
      return;
    }
    session.setToolHandler(onClientToolCall);
  }, [session, onClientToolCall, readOnly]);

  useEffect(() => {
    if (bootState.ready) {
      void session.refreshPanels(executionTarget, workspaceSupported);
    }
  }, [session, bootState.ready, executionTarget, workspaceSupported]);

  // Derive artifact index from the items stream (artifacts are now inline in conversation)
  const visibleArtifacts = useMemo(() => {
    return items.filter((it): it is ArtifactItem => it.type === 'artifact');
  }, [items]);

  // The canonical main plan is authoritative when the complete plans/diffs
  // feature was negotiated. Legacy task snapshots remain a compatibility
  // fallback only for older runtimes. Bash-job snapshots are unrelated and
  // continue to use their existing projection.
  const { rawTasks, tasks, derivedBashJobs } = useMemo(() => {
    let legacyTasks: TaskSummary[] = [];
    let lastJobs: BashJobSummary[] = [];
    for (const it of items) {
      if (it.type !== 'tool') {
        continue;
      }
      const md = (it as { metadata?: { tasks_snapshot?: unknown; bash_jobs_snapshot?: unknown } }).metadata;
      if (Array.isArray(md?.tasks_snapshot)) {
        legacyTasks = md.tasks_snapshot as TaskSummary[];
      }
      if (Array.isArray(md?.bash_jobs_snapshot)) {
        lastJobs = md.bash_jobs_snapshot as BashJobSummary[];
      }
    }
    const planFeatureSupported = connected && client.supportsExperimentalFeature('plansAndDiffs');
    const lastTasks = negotiatedPlanTasks(items, legacyTasks, planFeatureSupported);
    // Filter out completed tasks that were dismissed at the last user
    // submit, then apply the idle-mode "hide completed" rule on top so
    // the panel quiets down between runs.
    const liveTasks = lastTasks.filter((t) => !(t.status === 'completed' && dismissedTaskIds.has(t.id)));
    const filteredTasks = runActive ? liveTasks : liveTasks.filter((t) => t.status !== 'completed');
    return { rawTasks: lastTasks, tasks: filteredTasks, derivedBashJobs: lastJobs };
  }, [client, connected, items, runActive, dismissedTaskIds]);

  // Live override (from ui.bash_jobs.update broadcasts and bash_jobs.* server
  // calls) takes precedence over history-derived state when present. Mirror
  // the Tasks behavior: while a run is active keep everything visible (minus
  // dismissed exits), and once idle drop successful exits but keep failures
  // (non-zero/null exit_code) visible until the user dismisses them.
  const allBashJobs = liveBashJobs ?? derivedBashJobs;
  const bashJobs = useMemo(() => {
    const live = allBashJobs.filter((j) => !(!j.running && dismissedJobIds.has(j.job_id)));
    return runActive ? live : live.filter((j) => j.running || j.exit_code !== 0);
  }, [allBashJobs, runActive, dismissedJobIds]);

  // Same shape for subagents: drop dismissed worker exits, then idle-hide
  // only successful completions so failures (error/cancelled) stay visible
  // until the user dismisses them. Agent-tool entries can't be dismissed —
  // the server ages their ended tail out of the snapshot.
  const visibleSubagents = useMemo(() => {
    const live = subagents.filter(
      (s) => !(s.status !== 'running' && s.worker_id && dismissedWorkerIds.has(s.worker_id))
    );
    return runActive ? live : live.filter((s) => s.status !== 'completed');
  }, [subagents, runActive, dismissedWorkerIds]);

  // Mirror bash jobs into the activity store (unfiltered — the Agents
  // surface shows truth; dismissal only quiets the pill row).
  useEffect(() => {
    if (sessionId) {
      publishBashJobs(sessionId, allBashJobs);
    }
  }, [sessionId, allBashJobs]);

  const handleWorkerKill = useCallback(
    async (worker_id: string): Promise<WorkersKillResult> => {
      if (!environmentId) {
        throw new Error('Execution environment unavailable');
      }
      const res = (await client.serverCall(
        'workers.kill',
        { worker_id },
        sessionId,
        executionTarget
      )) as unknown as WorkersKillResult;
      if (Array.isArray(res?.snapshot) && sessionId) {
        // ``workers.kill`` snapshots workers only; the merge keeps the
        // agent-tool entries until the next bus broadcast.
        mergeWorkersSnapshot(sessionId, normalizeSubagentSnapshot(res.snapshot));
      }
      return res;
    },
    [client, executionTarget, sessionId]
  );

  const handleWorkerResume = useCallback(
    async (worker_id: string, operation_id: string): Promise<WorkersKillResult> => {
      const res = (await client.serverCall(
        'workers.resume',
        { worker_id, operation_id },
        sessionId,
        executionTarget
      )) as unknown as WorkersKillResult;
      if (Array.isArray(res?.snapshot) && sessionId) {
        mergeWorkersSnapshot(sessionId, normalizeSubagentSnapshot(res.snapshot));
      }
      return res;
    },
    [client, executionTarget, sessionId]
  );

  const handleBashKill = useCallback(
    async (job_id: string): Promise<BashJobsKillResult> => {
      if (!environmentId) {
        throw new Error('Execution environment unavailable');
      }
      const res = (await client.serverCall(
        'bash_jobs.kill',
        { job_id },
        sessionId,
        executionTarget
      )) as unknown as BashJobsKillResult;
      if (Array.isArray(res?.snapshot)) {
        setLiveBashJobs(res.snapshot);
      }
      return res;
    },
    [client, executionTarget, sessionId]
  );

  const handleBashTail = useCallback(
    async (job_id: string, lines?: number): Promise<BashJobsTailResult> => {
      if (!environmentId) {
        throw new Error('Execution environment unavailable');
      }
      const args: Record<string, unknown> = { job_id };
      if (typeof lines === 'number') {
        args.lines = lines;
      }
      const res = (await client.serverCall(
        'bash_jobs.tail',
        args,
        sessionId,
        executionTarget
      )) as unknown as BashJobsTailResult & {
        snapshot?: BashJobSummary[];
      };
      if (Array.isArray(res?.snapshot)) {
        setLiveBashJobs(res.snapshot);
      }
      return res;
    },
    [client, executionTarget, sessionId]
  );

  const handleWorkerPlan = useCallback(
    async (workerSessionId: string): Promise<TaskSummary[] | null> => {
      // Workers run their own sessions on the same `omni serve`; `get_plan`
      // is thread-addressed, so this session's connection can read a
      // worker's main plan with no new protocol (worker plans stay private
      // to the worker — this is read-only observability).
      const result = parsePlanResult(await client.request('get_plan', { thread_id: workerSessionId, scope: 'main' }));
      return rpcPlanTasks(result.plan);
    },
    [client]
  );

  const handleBashWarmup = useCallback(async () => {
    if (!environmentId) {
      throw new Error('Execution environment unavailable');
    }
    const res = (await client.serverCall('bash_jobs.list', {}, sessionId, executionTarget)) as unknown as {
      snapshot?: BashJobSummary[];
    };
    if (Array.isArray(res?.snapshot)) {
      setLiveBashJobs(res.snapshot);
    }
  }, [client, executionTarget, sessionId]);

  // The Agents sidecar surface and the pill popovers live outside this
  // React tree; they stop/tail through the per-session action registry
  // instead of props.
  useEffect(() => {
    if (!sessionId || readOnly) {
      return;
    }
    registerActivityActions(sessionId, {
      killWorker: handleWorkerKill,
      resumeWorker: handleWorkerResume,
      killJob: handleBashKill,
      tailJob: handleBashTail,
      getWorkerPlan: handleWorkerPlan,
    });
    return () => registerActivityActions(sessionId, null);
  }, [sessionId, readOnly, handleWorkerKill, handleWorkerResume, handleBashKill, handleBashTail, handleWorkerPlan]);

  // Keep the Review sidecar's "This turn" scope current: the newest
  // run_diff transcript item is the session's turn record.
  useEffect(() => {
    if (!sessionId) {
      return;
    }
    const last = items.findLast((item) => item.type === 'run_diff');
    if (last) {
      publishRunDiff(sessionId, last as RunDiffItem);
    }
  }, [items, sessionId]);

  // Review on a specific card pins that run's record (an older card
  // reviews its own turn, not the latest) before opening the app.
  const handleOpenReview = useCallback(
    (item: RunDiffItem) => {
      if (sessionId) {
        publishRunDiff(sessionId, item);
      }
      onOpenApp?.('review');
    },
    [sessionId, onOpenApp]
  );

  // Once per session with a running job, poke ``bash_jobs.list`` so the
  // server-side sweeper captures a service handle and starts pushing
  // ``ui.bash_jobs.update`` on natural exits. (Previously the docked
  // BashJobs panel fired this on mount.)
  const bashWarmupSessionRef = useRef<string | null>(null);
  useEffect(() => {
    if (!sessionId || readOnly || bashWarmupSessionRef.current === sessionId) {
      return;
    }
    if (!allBashJobs.some((j) => j.running)) {
      return;
    }
    bashWarmupSessionRef.current = sessionId;
    handleBashWarmup().catch(() => {
      bashWarmupSessionRef.current = null;
    });
  }, [sessionId, readOnly, allBashJobs, handleBashWarmup]);

  const handleBashDismiss = useCallback((job_id: string) => {
    setDismissedJobIds((prev) => {
      const next = new Set(prev);
      next.add(job_id);
      return next;
    });
  }, []);

  const handleWorkerDismiss = useCallback((worker_id: string) => {
    setDismissedWorkerIds((prev) => {
      const next = new Set(prev);
      next.add(worker_id);
      return next;
    });
  }, []);

  const handleGoalDismiss = useCallback(() => setGoalSnapshot(null), []);
  const handleWakeupDismiss = useCallback(() => setWakeupSnapshot(null), []);
  const handleLoopDismiss = useCallback(() => setLoopTasks([]), []);

  useEffect(() => {
    const handler = () => setIsLargeScreen(window.innerWidth >= 1024);
    window.addEventListener('resize', handler);
    return () => window.removeEventListener('resize', handler);
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem('artifacts-panel-width', String(artifactsPanelWidth));
    } catch {}
  }, [artifactsPanelWidth]);

  // Scroll to an inline artifact in the conversation stream
  const handleScrollToArtifact = useCallback(
    (artifactId: string) => {
      setArtifactsPanelOpen(false);
      const el = chatColumnEl?.querySelector(`[data-artifact-id="${CSS.escape(artifactId)}"]`);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        el.classList.add('ring-2', 'ring-primary', 'rounded-lg');
        setTimeout(() => el.classList.remove('ring-2', 'ring-primary', 'rounded-lg'), 1500);
      }
    },
    [chatColumnEl]
  );

  // moved below handleSubmit

  const handleSubmit = useCallback(
    async (
      text: string,
      files?: File[],
      runOverrides?: import('@omniagents/client/rpc/execution').RunOverrides,
      inputId?: string
    ): Promise<{ runId: string } | undefined> => {
      const submittingSession = actor.getSnapshot();
      if (
        !conversationIsReady({
          connected: client.isConnected,
          bootReady: bootState.actor.getSnapshot().matches('ready'),
          sessionReady: submittingSession.matches('ready'),
          sessionId: submittingSession.context.sessionId,
          expectedSessionId: sessionIdProp,
        })
      ) {
        throw new Error('Conversation is still connecting. Your message has been kept; please retry.');
      }
      // Dismiss successfully-completed items from each docked panel so the
      // next run starts with a clean dock. Failures (worker error/cancelled,
      // non-zero/null bash exit) stick until the user dismisses them so a
      // quietly-failed background task isn't lost behind the next prompt.
      // Items spawned during the new run aren't in the set yet and will stay
      // visible until the user's next submit. Fires for slash commands too
      // — a slash is still a user-initiated step boundary.
      setDismissedWorkerIds((prev) => {
        const next = new Set(prev);
        for (const w of subagents) {
          if (w.kind === 'worker' && w.worker_id && w.status === 'completed') {
            next.add(w.worker_id);
          }
        }
        return next;
      });
      setDismissedJobIds((prev) => {
        const next = new Set(prev);
        for (const j of allBashJobs) {
          if (!j.running && j.exit_code === 0) {
            next.add(j.job_id);
          }
        }
        return next;
      });
      setDismissedTaskIds((prev) => {
        const next = new Set(prev);
        for (const t of rawTasks) {
          if (t.status === 'completed') {
            next.add(t.id);
          }
        }
        return next;
      });
      setGoalSnapshot((prev) => {
        if (prev?.status === 'completed' || prev?.status === 'cancelled') {
          return null;
        }
        return prev;
      });

      const useVoiceRun = voiceRunRef.current;
      voiceRunRef.current = false;
      const variables = (speakRepliesEnabled || useVoiceRun) && voiceVariables ? voiceVariables : variablesProp;
      return session.send(text, files, {
        executionTarget,
        variables,
        overrides: runOverrides,
        workspaceSupported,
        inputId,
      });
    },
    [
      session,
      client,
      actor,
      bootState.actor,
      sessionIdProp,
      executionTarget,
      variablesProp,
      voiceVariables,
      speakRepliesEnabled,
      workspaceSupported,
      subagents,
      allBashJobs,
      rawTasks,
    ]
  );

  const handleVoiceSubmit = useCallback(
    (text: string) => {
      setSpeakRepliesEnabled(true);
      voiceRunRef.current = true;
      void handleSubmit(text).catch(() => {});
    },
    [handleSubmit]
  );

  const handleStop = useCallback(() => {
    void session.stopRun().catch(() => {});
  }, [session]);

  // ---------------------------------------------------------------------------
  // Scheduled tasks: the host starts a single run on this column's session via
  // the actor; the conversation streams into the live UI and tool approvals
  // surface here. Run/approval events forward back so the host can drive
  // routine history / status / toasts.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    const routines = host.routines;
    if (!routineId || !routines) {
      return;
    }

    const awaitChatReady = (): Promise<void> =>
      waitFor(
        actor,
        (s) => {
          const v = s.value;
          return typeof v === 'object' && v !== null && 'ready' in v && (v as { ready?: unknown }).ready === 'idle';
        },
        { timeout: 30_000 }
      ).then(() => undefined);

    const unregister = routines.register({
      taskId: routineId,
      startRun: async (prompt, safeToolOverrides) => {
        await awaitChatReady();
        const result = await handleSubmit(prompt, undefined, safeToolOverrides ? { safeToolOverrides } : undefined);
        if (!result?.runId) {
          throw new Error('start_run did not return a run_id');
        }
        return { runId: result.runId };
      },
      stop: async () => {
        await session.stopRun();
      },
    });

    return () => {
      unregister();
    };
  }, [routineId, client, machine, actor, handleSubmit, session, host]);

  // Forward routine run/approval events back to main's ScheduledTaskManager.
  useEffect(() => {
    const routines = host.routines;
    if (!routineId || !routines) {
      return;
    }
    type ApprovalEvent = { run_id?: unknown; end_reason?: unknown; tool_name?: unknown; server_label?: unknown };
    const toolNameOf = (p: ApprovalEvent): string | undefined =>
      typeof p.tool_name === 'string' ? p.tool_name : undefined;
    const offs: Array<() => void> = [];

    offs.push(
      session.on('run_started', (raw: unknown) => {
        const p = (raw ?? {}) as ApprovalEvent;
        routines.forward({ kind: 'run-started', taskId: routineId, runId: String(p.run_id ?? '') });
      })
    );
    offs.push(
      session.on('run_end', (raw: unknown) => {
        const p = (raw ?? {}) as ApprovalEvent;
        routines.forward({ kind: 'run-end', taskId: routineId, reason: String(p.end_reason ?? 'completed') });
      })
    );
    offs.push(
      session.on('tool_approval_requested', (raw: unknown) => {
        const p = (raw ?? {}) as ApprovalEvent;
        routines.forward({
          kind: 'approval-requested',
          taskId: routineId,
          approval: { kind: 'function', ...(toolNameOf(p) ? { toolName: toolNameOf(p) } : {}) },
        });
      })
    );
    offs.push(
      session.on('tool_approval_resolved', () => {
        routines.forward({ kind: 'approval-resolved', taskId: routineId });
      })
    );
    offs.push(
      session.on('mcp_approval_requested', (raw: unknown) => {
        const p = (raw ?? {}) as ApprovalEvent;
        routines.forward({
          kind: 'approval-requested',
          taskId: routineId,
          approval: {
            kind: 'mcp',
            ...(toolNameOf(p) ? { toolName: toolNameOf(p) } : {}),
            ...(typeof p.server_label === 'string' ? { serverLabel: p.server_label } : {}),
          },
        });
      })
    );
    offs.push(
      session.on('mcp_approval_resolved', () => {
        routines.forward({ kind: 'approval-resolved', taskId: routineId });
      })
    );

    return () => {
      for (const off of offs) {
        off();
      }
    };
  }, [routineId, client, host]);

  useEffect(() => {
    const initial = initialMessage;
    if (!initial || !conversationReady || ui !== 'chat' || items.length || !session.claimIntent('url-initial')) {
      return;
    }
    void handleSubmit(initial).catch((cause: unknown) => {
      const draft = getConversationDraft(session.id);
      updateConversationDraft(session.id, {
        text: [initial, draft.text].filter(Boolean).join('\n\n'),
      });
      host.notifyError('Message not sent', cause instanceof Error ? cause.message : undefined);
    });
  }, [session, conversationReady, ui, items.length, handleSubmit, initialMessage]);

  // A failed hydration is retried on its own, as soon as the connection is
  // back and then on a short backoff. The composer stays disabled meanwhile;
  // there is nothing for the user to click.
  useEffect(() => {
    if (machine.phase !== 'initError' || !connected || !bootState.ready) {
      return;
    }
    const timer = setTimeout(() => void loadSession(sessionId).catch(() => {}), 1500);
    return () => clearTimeout(timer);
  }, [machine.phase, connected, bootState.ready, sessionId, loadSession]);

  // Flush messages queued from ChatShell before the backend was ready
  const pendingFlushedRef = useRef(false);
  useEffect(() => {
    if (pendingMessages && pendingMessages.length > 0) {
      console.info(
        `[pending-intent] receiver session=${sessionIdProp ?? 'none'} count=${pendingMessages.length} connected=${connected} ui=${ui}`
      );
    }
    if (pendingFlushedRef.current) {
      return;
    }
    if (
      !connected ||
      !bootState.ready ||
      machine.phase === 'initializing' ||
      machine.phase === 'initError' ||
      ui !== 'chat'
    ) {
      return;
    }
    if (!pendingMessages || pendingMessages.length === 0) {
      return;
    }
    pendingFlushedRef.current = true;
    const claimedMessages = pendingMessages;
    // Ownership moves from the launch shell to this mounted chat before any
    // asynchronous submission begins. If the RPC provider remounts during a
    // run, the old preview cannot be claimed and submitted a second time.
    onPendingMessagesFlushed?.();
    void (async () => {
      for (let index = 0; index < claimedMessages.length; index++) {
        const msg = claimedMessages[index]!;
        try {
          await handleSubmit(msg.text, msg.files, undefined, msg.inputId);
        } catch (cause) {
          const id = sessionIdProp ?? sessionId;
          if (id) {
            const remaining = claimedMessages.slice(index);
            const draft = getConversationDraft(id);
            updateConversationDraft(id, {
              text: [...remaining.map((message) => message.text), draft.text].filter(Boolean).join('\n\n'),
              files: [...remaining.flatMap((message) => message.files ?? []), ...draft.files],
            });
            host.notifyError('Message not sent', cause instanceof Error ? cause.message : undefined);
          }
          break;
        }
      }
    })();
  }, [
    connected,
    bootState.ready,
    machine.phase,
    ui,
    pendingMessages,
    handleSubmit,
    onPendingMessagesFlushed,
    sessionIdProp,
    sessionId,
  ]);

  const handleApprovalDecision = useCallback(
    async (request_id: string, value: 'yes' | 'always' | 'no', kind: 'function' | 'mcp' = 'function') => {
      await respondToApproval(client, request_id, value, kind);
      approvalDecided(request_id, value);
    },
    [client, approvalDecided]
  );

  // Expose an imperative controller so the headless global orchestrator can
  // drive this column (send / approve / cancel) and read its run state via the
  // `column_*` tools. Refs keep the controller object stable while reading live
  // state each call. The parent (CodeWorkspaceLayout) registers it by tabId.
  // One cursor assigner per column, so transcript cursors are stable across
  // reads and survive approval removals / tool result updates.
  const cursorAssignerRef = useRef(createCursorAssigner());
  const ctrlStateRef = useRef<import('@omniagents/chat-ui/session-controller').ColumnRunState>({
    running: false,
    awaitingApproval: [],
    transcript: { total: 0, latestCursor: null },
  });
  const cursors = cursorAssignerRef.current.assign(items);
  ctrlStateRef.current = {
    running: runActive,
    runId: runId ?? undefined,
    awaitingApproval: items
      .filter((i) => i.type === 'approval')
      .map((i) => {
        const a = i as { request_id: string; kind?: 'function' | 'mcp'; tool?: string };
        return { requestId: a.request_id, kind: a.kind ?? 'function', tool: a.tool };
      }),
    transcript: {
      total: items.length,
      latestCursor: items.length > 0 ? (cursors[items.length - 1] ?? null) : null,
      last: lastEntrySignal(items, cursors),
    },
  };
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const handleSubmitRef = useRef(handleSubmit);
  handleSubmitRef.current = handleSubmit;
  const handleStopRef = useRef(handleStop);
  handleStopRef.current = handleStop;
  const handleApprovalRef = useRef(handleApprovalDecision);
  handleApprovalRef.current = handleApprovalDecision;
  // Assigned after `handleSelectSession` is defined (below); kept in a ref so the
  // controller object stays stable.
  const newSessionRef = useRef<() => void>(() => {});
  useEffect(() => {
    if (!onController) {
      return;
    }
    const controller: SessionController = {
      getState: () => ctrlStateRef.current,
      getTranscript: (opts?: { after?: number; before?: number; limit?: number }) =>
        transcriptPage(itemsRef.current, cursorAssignerRef.current.assign(itemsRef.current), opts),
      getEntry: (cursor: number) =>
        fullEntry(itemsRef.current, cursorAssignerRef.current.assign(itemsRef.current), cursor),
      sendMessage: (text: string) => handleSubmitRef.current(text),
      stopRun: () => handleStopRef.current(),
      decideApproval: (requestId: string, decision: 'approve' | 'reject') => {
        const pending = ctrlStateRef.current.awaitingApproval.find((p) => p.requestId === requestId);
        return handleApprovalRef.current(requestId, decision === 'approve' ? 'yes' : 'no', pending?.kind ?? 'function');
      },
      newSession: () => newSessionRef.current(),
      // Feeds the launcher's Projects and Recents sections — cap the server
      // response so a long history isn't serialized on every poll.
      listSessions: () => loadCanonicalSessionList(client, SESSION_LIST_LIMIT),
      notify: (content: string, source: string) =>
        // Deliver as a role="assistant" history item that triggers a run — the
        // exact wakeup the notification flusher uses internally. Uses the
        // existing `enqueue_message` RPC so it works against the released
        // runtime (no custom server function required).
        client.enqueueMessage(actor.getSnapshot().context.sessionId ?? '', content, {
          role: 'assistant',
          source,
        }),
    };
    onController(controller);
    return () => onController(null);
  }, [onController, client, actor]);

  const handleSelectSession = useCallback(
    async (id?: string) => {
      if (id !== session.id) {
        onSelectConversation(id);
        return;
      }
      try {
        await session.load({ force: true });
        await session.refreshPanels(executionTarget, workspaceSupported);
        setUI('chat');
      } catch {
        // The controller publishes initError for the retry surface.
      }
    },
    [session, onSelectConversation, executionTarget, workspaceSupported]
  );
  // New chat selects a fresh controller; the old controller keeps its identity.
  newSessionRef.current = () => void handleSelectSession(undefined);

  // When the voice session ends, reload the thread: the server persisted the
  // voice turns into canonical history, which replaces the machine's live
  // transcript items (cleared on close) without a duplication window.
  useEffect(() => {
    if (voiceWasActiveRef.current && !voiceIsActive) {
      const sid = actor.getSnapshot().context.sessionId;
      if (sid) {
        void handleSelectSession(sid);
      }
      refreshSessions();
    }
    voiceWasActiveRef.current = voiceIsActive;
  }, [voiceIsActive, actor, handleSelectSession, refreshSessions]);

  useEffect(() => {
    if (readyRef.current || !onReady) {
      return;
    }
    if (connected && (ui === 'chat' || ui === 'resume')) {
      readyRef.current = true;
      onReady();
    }
  }, [connected, onReady, ui]);

  const onNewChat = useCallback(() => {
    handleSelectSession(undefined);
  }, [handleSelectSession]);

  const handleReaction = useCallback(
    async (type: 'like' | 'dislike', text?: string) => {
      try {
        const func = type === 'like' ? 'good' : 'bad';
        const args: Record<string, unknown> = {};
        if (text) {
          args.text = text;
        }
        await client.serverCall(func, args, sessionId, executionTarget);
      } catch {}
    },
    [client, sessionId]
  );

  const hasArtifacts = visibleArtifacts.length > 0;
  const headerActions = {
    showArtifactsButton: hasArtifacts,
    onArtifactsToggle: hasArtifacts ? () => setArtifactsPanelOpen((v) => !v) : undefined,
  };

  let content: React.ReactNode = null;
  if (ui === 'error') {
    content = (
      <div className="app flex-col">
        <Header agentName={agentName} />
        <div className="flex flex-1 items-center justify-center p-6">
          <div className="max-w-md space-y-3 rounded-lg border bg-card p-5 text-center shadow-sm">
            <h2 className="text-base font-semibold">Couldn’t connect to Omniagents</h2>
            <p className="text-sm text-muted-foreground">{bootState.error || 'The agent runtime is unavailable.'}</p>
            <Button type="button" onClick={bootState.retry}>
              Try again
            </Button>
            <p
              role="status"
              aria-live="polite"
              className="flex items-center justify-center gap-1.5 text-xs text-muted-foreground"
            >
              <Spinner className="size-3" aria-hidden="true" />
              Still retrying automatically…
            </p>
          </div>
        </div>
      </div>
    );
  } else if (ui === 'resume') {
    content = (
      <div className="app flex-col">
        <Header agentName={agentName} />
        <div className="container-chat">
          <SessionList sessions={sessions} onSelect={handleSelectSession} />
        </div>
      </div>
    );
  } else {
    content = (
      <div
        className="app h-full flex flex-row min-w-0 relative"
        // The session's run lifecycle, readable from outside: "idle" only once
        // the session is connected, loaded and has no run in flight. Controls
        // like Stop also disappear while a reconnecting session reloads, so
        // their absence does not mean a run ended.
        data-run-state={connected ? machine.phase : 'disconnected'}
      >
        {!minimalMode && (
          <Sidebar
            open={sidebarOpen}
            sessions={sessions}
            selectedId={sessionId}
            onClose={() => setSidebarOpen(false)}
            onNewChat={onNewChat}
            onSelect={(id) => handleSelectSession(id)}
            managementSupported={managementSupported}
            searchResults={searchResults}
            searching={conversationsSearching}
            onSearchQueryChange={setSearchQuery}
            busyThreadIds={busyThreadIds}
            operationError={conversationOperationError}
            onDismissOperationError={dismissConversationOperationError}
            onRename={renameThread}
            onSetPinned={setThreadPinned}
            onArchive={archiveThread}
            onRestore={restoreThread}
          />
        )}
        <div className="flex-1 flex flex-col min-h-0 min-w-0">
          {!minimalMode && (
            <Header
              agentName={agentName}
              onMenu={() => setSidebarOpen((v) => !v)}
              onArtifactsToggle={headerActions.onArtifactsToggle}
              showArtifactsButton={headerActions.showArtifactsButton}
            />
          )}
          <ResizablePanelGroup orientation="horizontal" className="min-h-0 min-w-0 flex-1">
            <ResizablePanel minSize="50%">
              <div className="flex h-full min-h-0 min-w-0 flex-col">
                <div ref={setChatColumnEl} className="flex-1 min-h-0 min-w-0 overflow-x-hidden relative flex flex-col">
                  <ArtifactPortalProvider target={chatColumnEl}>
                    <MessageList
                      items={mergedItems}
                      greeting={greeting}
                      suggestions={suggestions}
                      statusText={status}
                      thinking={thinking}
                      statusSpinner={statusSpinner}
                      welcomeText={welcomeText}
                      onApprovalDecision={handleApprovalDecision}
                      pendingPlan={pendingPlan}
                      onPlanDecision={onPlanDecision}
                      statusItalic={statusItalic}
                      onReaction={handleReaction}
                      currentRunId={runId}
                      toolStatusText={toolStatus}
                      onSubmitMessage={(text) => {
                        void handleSubmit(text).catch((cause: unknown) => {
                          if (sessionId) {
                            const draft = getConversationDraft(sessionId);
                            updateConversationDraft(sessionId, {
                              text: [text, draft.text].filter(Boolean).join('\n\n'),
                            });
                          }
                          host.notifyError('Message not sent', cause instanceof Error ? cause.message : undefined);
                        });
                      }}
                      onStageContext={stageContext}
                      onOpenReview={onOpenApp ? handleOpenReview : undefined}
                    />
                  </ArtifactPortalProvider>
                  <AnimatePresence>
                    {!connected && !bootRetry.retrying && (
                      <motion.div
                        className="absolute bottom-2 left-0 right-0 flex justify-center pointer-events-none z-10"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.3, ease: 'easeOut' }}
                      >
                        <div className="inline-flex items-center gap-1.5 rounded-full bg-accent px-3 py-1">
                          <Spinner className="size-3 text-muted-foreground" aria-hidden="true" />
                          <span className="text-xs text-muted-foreground">Connecting…</span>
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
                <GoalPanel snapshot={goalSnapshot} onDismiss={handleGoalDismiss} />
                <WakeupPanel snapshot={wakeupSnapshot} onDismiss={handleWakeupDismiss} />
                <LoopPanel tasks={loopTasks} onDismiss={handleLoopDismiss} />
                <QueuedMessages
                  items={queuedMessages}
                  onCancel={(id) => {
                    if (!sessionId) {
                      return;
                    }
                    // Optimistic remove — the server's queue_changed broadcast
                    // is the source of truth and will overwrite this if the
                    // cancel raced with a drainer pop (cancel returns not_found).
                    setQueuedMessages((prev) => prev.filter((it) => it.id !== id));
                    client.cancelQueuedMessage(sessionId, id).catch(() => {});
                  }}
                />
                <Notifications
                  notifications={notifications}
                  onDismiss={(id) => setNotifications((prev) => prev.filter((n) => n.id !== id))}
                  onDismissAll={() => setNotifications([])}
                />
                <RecapPanel recap={recap} onDismiss={() => setRecap(null)} />
                <EscalationBanner escalation={escalation} />
                {elicitations.map((request) => (
                  <ElicitationCard
                    key={request.elicitationId}
                    request={request}
                    onRespond={(response) => respondToElicitation(request, response)}
                  />
                ))}
                {stagedContext.length > 0 && (
                  // MCP-Apps staged context chips. Each ``ui/update-model-context``
                  // entry shows up here so the user knows what'll be sent on the
                  // next turn; clicking × removes that entry (passing empty text
                  // to ``stageContext`` clears the source).
                  <div className="flex flex-wrap gap-1 px-3 py-1 text-xs">
                    {stagedContext.map((c) => (
                      <span
                        key={c.source}
                        className="inline-flex items-center gap-1 rounded-full border border-border bg-secondary px-2 py-0.5 text-muted-foreground"
                        title={c.text}
                      >
                        <span className="max-w-60 truncate">
                          📎 {c.text.slice(0, 60)}
                          {c.text.length > 60 ? '…' : ''}
                        </span>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-xs"
                          onClick={() => stageContext(c.source, '')}
                          aria-label="Remove staged context"
                        >
                          ×
                        </Button>
                      </span>
                    ))}
                  </div>
                )}
                {!readOnly && (
                  <>
                    {/* Above the pills: the dock is the session's headline
                        while a call is live, not another composer row. */}
                    <VoiceDock voice={voice} />
                    <PillStrip
                      sessionId={sessionId}
                      subagents={visibleSubagents}
                      tasks={tasks}
                      jobs={bashJobs}
                      onOpenAgents={onOpenApp ? () => onOpenApp('agents') : undefined}
                      onOpenJobs={onOpenApp ? () => onOpenApp('jobs') : undefined}
                      onWorkerKill={handleWorkerKill}
                      onWorkerDismiss={handleWorkerDismiss}
                      onJobKill={handleBashKill}
                      onJobDismiss={handleBashDismiss}
                    >
                      {sessionId ? (
                        <ModelSessionControls
                          session={session}
                          key={sessionId}
                          connected={
                            connected &&
                            bootState.ready &&
                            machine.phase !== 'initializing' &&
                            machine.phase !== 'initError'
                          }
                          sessionId={sessionId}
                          transport={client}
                          disabled={runActive}
                          approvalsSupported={client.supportsExperimentalFeature('approvalReviewer')}
                          onSetApprovalsReviewer={(reviewer) => client.setSessionApprovals(sessionId!, reviewer)}
                          workflowSupported={client.supportsExperimentalFeature('workflowReviewer')}
                          onSetWorkflowReviewer={(reviewer) => client.setSessionWorkflow(sessionId!, reviewer)}
                        />
                      ) : null}
                    </PillStrip>
                    {bootRetry.retrying && (
                      <div
                        role="status"
                        aria-live="polite"
                        className="flex items-center gap-1.5 px-3 py-1 text-xs text-muted-foreground"
                      >
                        <Spinner className="size-3" aria-hidden="true" />
                        <span>{ui === 'connecting' ? 'Connecting…' : 'Reconnecting…'}</span>
                      </div>
                    )}
                    <ConversationComposer
                      key={sessionIdProp ?? sessionId}
                      conversationId={sessionIdProp ?? sessionId}
                      disabled={!conversationReady}
                      thinking={thinking || machine.phase === 'awaitingApproval'}
                      onStop={handleStop}
                      onSubmit={(text, files, inputId) => {
                        // A live voice session owns the conversation: typed
                        // text (without attachments) routes into it rather
                        // than starting a parallel text run on the same
                        // session/environment.
                        if (voiceIsActive && !files?.length && voice.sendText(text)) {
                          return;
                        }
                        return handleSubmit(text, files, undefined, inputId);
                      }}
                      onVoiceSubmit={handleVoiceSubmit}
                      voiceEnabled={voiceEnabled}
                      speakRepliesEnabled={!!voiceVariables && speakRepliesEnabled}
                      onSpeakRepliesChange={setSpeakRepliesEnabled}
                      workspacePath={workspaceSupported ? workspacePath : undefined}
                      composerExtras={composerExtras}
                      onVoiceStart={() => voice.open(actor.getSnapshot().context.sessionId)}
                      onVoiceEnd={voice.close}
                      onVoiceToggleMute={voice.toggleMute}
                      voiceMuted={voiceMuted}
                      voiceLive={voiceIsActive}
                    />
                  </>
                )}
              </div>
            </ResizablePanel>
            {isLargeScreen && artifactsPanelOpen && hasArtifacts && (
              <>
                <ResizableHandle />
                <ResizablePanel
                  id="artifacts"
                  defaultSize={artifactsPanelWidth}
                  minSize={180}
                  maxSize={400}
                  groupResizeBehavior="preserve-pixel-size"
                  onResize={(size) => setArtifactsPanelWidth(size.inPixels)}
                >
                  <div className="h-full min-h-0 border-l border-border">
                    <ArtifactsPanel
                      artifacts={visibleArtifacts}
                      onClose={() => setArtifactsPanelOpen(false)}
                      onScrollTo={handleScrollToArtifact}
                    />
                  </div>
                </ResizablePanel>
              </>
            )}
          </ResizablePanelGroup>
        </div>
        {!isLargeScreen && artifactsPanelOpen && hasArtifacts && (
          <ArtifactsPanel
            artifacts={visibleArtifacts}
            onClose={() => setArtifactsPanelOpen(false)}
            onScrollTo={handleScrollToArtifact}
            asOverlay
          />
        )}
      </div>
    );
  }

  return (
    <OmniAgentsHeaderActionsProvider {...headerActions}>
      {content}
      {headerActionsTargetId ? (
        <OmniAgentsHeaderActionsPortal targetId={headerActionsTargetId} compact={headerActionsCompact} />
      ) : null}
    </OmniAgentsHeaderActionsProvider>
  );
}
