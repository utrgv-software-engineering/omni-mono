import { useStore } from '@nanostores/react';
import { motion } from 'framer-motion';
import { FolderOpen } from 'lucide-react';
import { memo, useCallback, useEffect, useMemo, useState } from 'react';

import { conversationTitle } from '@/lib/chat-conversations';
import { buildSessionVariables } from '@/lib/client-tools';
import { uuidv4 } from '@/lib/uuid';
import { cn } from '@/renderer/ds/cn';
import { Button } from '@/renderer/ds/ui/button';
import { Spinner } from '@/renderer/ds/ui/spinner';
import { SessionStatusBanner } from '@/renderer/features/Banner/SessionStatusBanner';
import { buildClientToolHandler } from '@/renderer/features/Code/client-tool-handler';
import { $pendingPlan, resolvePlanApproval } from '@/renderer/features/Code/plan-approval-bridge';
import { openSettingsTab } from '@/renderer/features/SettingsModal/settings-nav';
import { useAutoRetry } from '@/renderer/hooks/use-auto-retry';
import { useSessionWorkspaceDir } from '@/renderer/hooks/use-session-workspace-dir';
import type { ClientToolCallHandler } from '@/renderer/omniagents-ui/App';
import { ChatShell, type PendingMessage } from '@/renderer/omniagents-ui/ChatShell';
import { ConversationComposerProvider } from '@/renderer/omniagents-ui/components/ConversationComposer';
import { DraftSessionControls } from '@/renderer/omniagents-ui/components/DraftSessionControls';
import { getGreeting } from '@/renderer/omniagents-ui/greeting';
import { $agentStatuses } from '@/renderer/services/agent-process';
import { emitter, serverOrigin } from '@/renderer/services/ipc';
import { persistedStoreApi } from '@/renderer/services/store';
import { $hoveredVoiceScope, VoiceScopeContext } from '@/renderer/services/voice-recording';
import type { AppId } from '@/shared/app-registry';
import type { CodeTab, CodeTabId, ExecutionTarget, WorkspaceMountDescriptor } from '@/shared/types';
import { firstSource, isChatColumn } from '@/shared/types';
import { getActivePersona } from '@/shared/voice-personas';

import { AttachProjectMenu } from './AttachProjectMenu';
import { CodeWorkspaceLayout } from './CodeWorkspaceLayout';
import { CHAT_SUGGESTIONS, COLUMN_SUGGESTIONS } from './empty-suggestions';
import { $codeTabErrors, codeApi } from './state';
import { useCodeAutoLaunch } from './use-code-auto-launch';
import { workspaceScopeFromMounts } from './workspace-mounts';

const CodeErrorView = memo(({ tabId, retry }: { tabId: CodeTabId; retry: () => void }) => {
  const allErrors = useStore($codeTabErrors);
  const error = allErrors[tabId] ?? null;

  return (
    <div className="w-full h-full flex items-center justify-center">
      <div className="max-w-112 text-center pl-5 pr-5">
        <div className="text-base font-medium text-foreground">{error ?? 'Something went wrong'}</div>
        <div className="mt-5">
          <Button onClick={retry}>Retry</Button>
        </div>
      </div>
    </div>
  );
});
CodeErrorView.displayName = 'CodeErrorView';

const CodeRunningView = memo(
  ({
    runtime,
    executionTarget,
    sessionId,
    onSessionChange,
    variables,
    voiceVariables,
    activeApp,
    onActiveAppChange,
    onReady,
    uiMinimal,
    composerExtras,
    onClientToolCall,
    tabId,
    agentWorkspaceDir,
    workspaceRootPrefix,
    workspaceMounts,
    filesHost,
    gitHost,
    reviewHost,
    routineId,
    greeting,
    suggestions,
    pendingMessages,
    onPendingMessagesFlushed,
  }: {
    runtime: {
      uiUrl: string;
      authToken?: string;
    };
    executionTarget?: ExecutionTarget;
    sessionId?: string;
    onSessionChange?: (sessionId: string | undefined) => void;
    variables?: Record<string, unknown>;
    voiceVariables?: Record<string, unknown>;
    activeApp: AppId;
    onActiveAppChange?: (app: AppId) => void;
    onReady: () => void;
    uiMinimal?: boolean;
    composerExtras?: React.ReactNode;
    onClientToolCall?: ClientToolCallHandler;
    tabId?: string;
    agentWorkspaceDir?: string;
    /** Single-mount scope for the workspace sidecar surfaces. */
    workspaceRootPrefix?: string;
    /** Authoritative mount table for the environment. */
    workspaceMounts?: WorkspaceMountDescriptor[];
    filesHost: HTMLDivElement;
    gitHost: HTMLDivElement;
    reviewHost: HTMLDivElement;
    routineId?: string;
    /** Chat mode: time-of-day greeting shown on the empty conversation. */
    greeting?: string;
    /** One-tap example tasks shown on the empty conversation. */
    suggestions?: ReadonlyArray<{ label: string; prompt: string }>;
    /** Messages queued pre-launch; flushed by the app once its RPC connects. */
    pendingMessages?: PendingMessage[];
    onPendingMessagesFlushed?: () => void;
  }) => {
    const store = useStore(persistedStoreApi.$atom);
    const theme = store.theme ?? 'teams-light';
    const pendingPlan = useStore($pendingPlan);

    const runtimeConnection = useMemo(() => {
      // serverOrigin() returns the backend baseUrl in remote-linked Electron;
      // resolving the agent's relative /proxy/... against window.location
      // would (wrongly) anchor to localhost:5173 / file:// in that mode.
      const url = new URL(runtime.uiUrl, serverOrigin());
      if (theme !== 'default') {
        url.searchParams.set('theme', theme);
      }
      if (uiMinimal) {
        url.searchParams.set('minimal', 'true');
      }
      return { baseUrl: url.toString(), authToken: runtime.authToken };
    }, [runtime.authToken, runtime.uiUrl, theme, uiMinimal]);

    return (
      <div className="flex flex-col w-full h-full relative">
        <div className="flex-1 min-h-0 relative">
          <CodeWorkspaceLayout
            connection={runtimeConnection}
            executionTarget={executionTarget}
            sessionId={sessionId}
            onSessionChange={onSessionChange}
            variables={variables}
            voiceVariables={voiceVariables}
            activeApp={activeApp}
            onActiveAppChange={onActiveAppChange}
            onReady={onReady}
            composerExtras={composerExtras}
            onClientToolCall={onClientToolCall}
            pendingPlan={pendingPlan}
            onPlanDecision={resolvePlanApproval}
            tabId={tabId}
            agentWorkspaceDir={agentWorkspaceDir}
            workspaceRootPrefix={workspaceRootPrefix}
            workspaceMounts={workspaceMounts}
            filesHost={filesHost}
            gitHost={gitHost}
            reviewHost={reviewHost}
            routineId={routineId}
            greeting={greeting}
            suggestions={suggestions}
            pendingMessages={pendingMessages}
            onPendingMessagesFlushed={onPendingMessagesFlushed}
          />
        </div>
      </div>
    );
  }
);
CodeRunningView.displayName = 'CodeRunningView';

type CodeTabContentProps = {
  tab: CodeTab;
  isVisible: boolean;
  activeApp?: AppId;
  onActiveAppChange?: (app: AppId) => void;
  uiMinimal?: boolean;
  filesHost: HTMLDivElement;
  gitHost: HTMLDivElement;
  reviewHost: HTMLDivElement;
};

export const CodeTabContent = memo(
  ({
    tab,
    isVisible,
    activeApp = 'chat',
    onActiveAppChange,
    uiMinimal,
    filesHost,
    gitHost,
    reviewHost,
  }: CodeTabContentProps) => {
    const store = useStore(persistedStoreApi.$atom);
    // Chat mode is derived, not reserved: any projectless session column runs
    // as an ambient chat with a per-conversation scratch workspace. Attaching
    // a project converts it in place.
    const chatMode = isChatColumn(tab);
    const project = useMemo(
      () => store.projects.find((p) => p.id === tab.projectId) ?? null,
      [store.projects, tab.projectId]
    );
    // Projects without a linked local source still have a managed directory on
    // disk (`Projects/<slug>/` or `~/Omni/Workspace/` for Personal). Resolve it
    // lazily so the agent can start even when the user hasn't picked a
    // workspace.
    const projectSource = firstSource(project);
    const linkedWorkspaceDir = projectSource?.kind === 'local' ? projectSource.workspaceDir : null;
    const [resolvedProjectDir, setResolvedProjectDir] = useState<string | null>(null);
    useEffect(() => {
      if (linkedWorkspaceDir || !tab.projectId) {
        setResolvedProjectDir(null);
        return;
      }
      let cancelled = false;
      void emitter.invoke('project:get-dir', tab.projectId).then((dir) => {
        if (!cancelled) {
          setResolvedProjectDir(dir);
        }
      });
      return () => {
        cancelled = true;
      };
    }, [tab.projectId, linkedWorkspaceDir]);
    // Chat: mint the conversation id on the record if absent. It keys chat
    // history and the scratch directory, never the Workspace snapshot.
    useEffect(() => {
      if (chatMode && !tab.sessionId) {
        void codeApi.setTabSessionId(tab.id, uuidv4());
      }
    }, [chatMode, tab.sessionId, tab.id]);

    // Chat is an ambient surface, not a project — each conversation gets an
    // isolated `<workspaceDir>/Sessions/<sessionId>` scratch dir. Switching
    // conversations changes the workspace, which useAutoLaunch's reset effect
    // turns into an agent restart. Hook is called unconditionally (null base
    // for non-chat tabs) to keep hook order static.
    //
    // Lazy launch: the base dir is withheld until the column is ACTIVATED
    // (first message sent or migration stamp) — a null workspaceDir keeps
    // useAutoLaunch parked in idle, so creating a chat column costs nothing.
    const chatScratchDir = useSessionWorkspaceDir(
      chatMode && tab.sessionId && tab.activatedAt ? (store.workspaceDir ?? null) : null,
      tab.sessionId ?? ''
    );

    const workspaceDir = chatMode ? chatScratchDir : (linkedWorkspaceDir ?? resolvedProjectDir);

    const localVoice = store.localVoiceEnabled;
    // Attach-project chip for projectless chats, rendered in the composer chip
    // row pre- and post-launch (same node both places, so the composer never
    // changes shape when the session starts). Attaching mid-conversation is
    // supported: it binds the project and relaunches; the transcript continues.
    const composerExtras = useMemo(
      () =>
        chatMode ? (
          <AttachProjectMenu
            tabId={tab.id}
            trigger={
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 min-w-0 gap-1.5 px-2 text-xs font-normal"
                title="Attach a project to this chat"
              >
                <FolderOpen className="size-3.5 shrink-0 text-primary" />
                <span className="max-w-24 truncate sm:max-w-50">Attach project</span>
              </Button>
            }
          />
        ) : undefined,
      [chatMode, tab.id]
    );

    const [greeting] = useState(getGreeting);
    const allLaunchErrors = useStore($codeTabErrors);

    // Messages typed before the agent is up. The first submit activates the
    // column (which triggers the launch); OmniAgentsApp flushes the queue the
    // moment its RPC connects, so nothing typed during boot is lost.
    const [pendingMessages, setPendingMessages] = useState<PendingMessage[]>([]);
    const handlePendingMessagesFlushed = useCallback(() => setPendingMessages([]), []);
    // A conversation switch (new chat / resume) must not leak the previous
    // conversation's queued messages into the new session.
    useEffect(() => {
      setPendingMessages([]);
    }, [tab.sessionId]);
    const handlePrelaunchSubmit = useCallback(
      (msg: PendingMessage) => {
        if (!store.workspaceDir) {
          openSettingsTab('Workspace');
          return;
        }
        setPendingMessages((prev) => [...prev, msg]);
        if (!tab.activatedAt) {
          // First intent: title the conversation and boot the agent.
          if (tab.sessionId && msg.text.trim()) {
            void codeApi.recordConversation(tab.sessionId, { title: conversationTitle(msg.text) });
          }
          void codeApi.setTabActivated(tab.id);
        }
      },
      [store.workspaceDir, tab.activatedAt, tab.sessionId, tab.id]
    );

    const { phase, retry } = useCodeAutoLaunch(tab.id, workspaceDir, {
      ...(tab.projectId ? { projectId: tab.projectId } : {}),
      ...(tab.sessionId ? { sessionId: tab.sessionId } : {}),
    });

    // A launch failure is retried quietly on a capped backoff; the column
    // keeps its launching look and only a long, continuous failure shows
    // the error surface (which keeps retrying behind it).
    // An agent that fails to start usually fails the same way again, so
    // give it a couple of quiet retries and then ask, rather than the full
    // minute a dropped connection gets.
    const launchRetry = useAutoRetry({
      failing: phase === 'error',
      healthy: phase === 'running' || phase === 'idle',
      retry,
      giveUpAfterMs: 10_000,
    });
    const launchFailed = phase === 'error' && launchRetry.exhausted;

    const allStatuses = useStore($agentStatuses);
    const agentStatus = allStatuses[tab.id];

    // Only mount the iframe on ``running``. ``connecting`` arrives the
    // moment omni-serve emits its JSON readiness line — that's before
    // uvicorn has actually bound the port, so loading the iframe there
    // briefly shows ERR_CONNECTION_REFUSED / a uvicorn error before the
    // real UI loads. ``agent-process.ts`` already gates ``running`` on
    // an HTTP+WS health probe, so by then the port is truly serving.
    const runtime = useMemo(() => {
      if (!agentStatus || agentStatus.type !== 'running') {
        return null;
      }
      return agentStatus.data;
    }, [agentStatus]);
    useEffect(() => {
      if (pendingMessages.length > 0) {
        console.info(
          `[pending-intent] owner tab=${tab.id} session=${tab.sessionId ?? 'none'} count=${pendingMessages.length} runtime=${runtime ? 'running' : 'waiting'}`
        );
      }
    }, [pendingMessages, runtime, tab.id, tab.sessionId]);
    const executionTarget = useMemo(
      () =>
        runtime?.workspaceId && runtime.environmentId && runtime.environmentGeneration !== undefined
          ? {
              workspaceId: runtime.workspaceId,
              environmentId: runtime.environmentId,
              environmentGeneration: runtime.environmentGeneration,
            }
          : undefined,
      [runtime?.workspaceId, runtime?.environmentId, runtime?.environmentGeneration]
    );
    // The provisioned environment owns this value. Renderer guesses were the
    // source of Host/Devbox drift whenever source layouts differed.
    const agentWorkspaceDir = runtime?.workspaceRoot;

    // Scope the Files/Git/Review surfaces from the environment's
    // authoritative mount table: a single mount that landed under the root
    // (container `/workspace/<mountName>`; the chat wrapper named by the
    // session id) collapses so paths read `index.html`, not
    // `<session-id>/index.html`. Multi-mount environments keep the
    // composite root — there the mount level is real information.
    const workspaceRootPrefix = useMemo(() => workspaceScopeFromMounts(runtime?.mounts), [runtime?.mounts]);

    const handleSessionChange = useCallback(
      (sessionId: string | undefined) => {
        codeApi.setTabSessionId(tab.id, sessionId);
      },
      [tab.id]
    );

    const handleClientToolCall = useMemo(
      () =>
        buildClientToolHandler({
          tabId: tab.id,
        }),
      [tab.id]
    );

    const baseSessionArgs = useMemo(() => {
      if (chatMode) {
        // Chat is projectless but never folder-less: the per-conversation
        // scratch dir is its workspace, and passing it makes the output
        // guidance ("save deliverables in your working folder") apply.
        return {
          surface: 'chat' as const,
          ...(workspaceDir ? { context: { workspaceDir } } : {}),
        };
      }
      return {
        surface: 'code' as const,
        context: {
          ...(project ? { projectId: project.id, projectLabel: project.label, sources: project.sources } : {}),
        },
      };
    }, [chatMode, workspaceDir, project]);

    // Base runs are speak-free; the mic button arms the voice variant per-run.
    const clientToolVariables = useMemo(() => buildSessionVariables(baseSessionArgs), [baseSessionArgs]);
    const personaInstructions = getActivePersona(store).instructions;
    // Track pointer hover per column so the voice-toggle hotkey targets it.
    const onColumnMouseEnter = useCallback(() => $hoveredVoiceScope.set(tab.id), [tab.id]);
    const onColumnMouseLeave = useCallback(() => {
      if ($hoveredVoiceScope.get() === tab.id) {
        $hoveredVoiceScope.set(null);
      }
    }, [tab.id]);
    useEffect(() => () => onColumnMouseLeave(), [onColumnMouseLeave]);
    const voiceVariables = useMemo(
      () => (localVoice ? buildSessionVariables({ ...baseSessionArgs, voice: true, personaInstructions }) : undefined),
      [baseSessionArgs, localVoice, personaInstructions]
    );

    return (
      <VoiceScopeContext.Provider value={tab.id}>
        <ConversationComposerProvider>
          <div
            className={cn('w-full h-full relative', !isVisible && 'invisible pointer-events-none')}
            inert={!isVisible}
            aria-hidden={!isVisible}
            data-voice-scope={tab.id}
            onMouseEnter={onColumnMouseEnter}
            onMouseLeave={onColumnMouseLeave}
          >
            <SessionStatusBanner status={agentStatus} />
            {runtime ? (
              <VoiceScopeContext.Provider value={tab.id}>
                <CodeRunningView
                  runtime={runtime}
                  executionTarget={executionTarget}
                  sessionId={tab.sessionId}
                  onSessionChange={handleSessionChange}
                  variables={clientToolVariables}
                  voiceVariables={voiceVariables}
                  activeApp={activeApp}
                  onActiveAppChange={onActiveAppChange}
                  onReady={() => {}}
                  uiMinimal={uiMinimal}
                  composerExtras={composerExtras}
                  onClientToolCall={handleClientToolCall}
                  tabId={tab.id}
                  agentWorkspaceDir={agentWorkspaceDir}
                  workspaceRootPrefix={workspaceRootPrefix}
                  workspaceMounts={runtime?.mounts}
                  filesHost={filesHost}
                  gitHost={gitHost}
                  reviewHost={reviewHost}
                  routineId={tab.routineId}
                  greeting={chatMode ? greeting : undefined}
                  suggestions={chatMode ? CHAT_SUGGESTIONS : COLUMN_SUGGESTIONS}
                  pendingMessages={chatMode ? pendingMessages : undefined}
                  onPendingMessagesFlushed={chatMode ? handlePendingMessagesFlushed : undefined}
                />
              </VoiceScopeContext.Provider>
            ) : chatMode ? (
              /* Chat pre-launch / launching / error — the greeting shell. The
             composer is live the whole time: the first submit activates the
             column (lazy launch) and queues the message; queued messages
             flush into the session once the agent connects. */
              <ChatShell
                conversationId={tab.sessionId}
                sessionControls={
                  tab.sessionId ? (
                    <DraftSessionControls sessionId={tab.sessionId} disabled={Boolean(tab.activatedAt)} />
                  ) : undefined
                }
                greeting={greeting}
                phase={launchFailed ? 'error' : phase === 'idle' && !tab.activatedAt ? 'idle' : 'loading'}
                error={launchFailed ? (allLaunchErrors[tab.id] ?? undefined) : undefined}
                onRetry={launchFailed ? retry : undefined}
                onSubmit={handlePrelaunchSubmit}
                pendingMessages={pendingMessages}
                suggestions={!tab.activatedAt ? CHAT_SUGGESTIONS : undefined}
                composerExtras={composerExtras}
                workspaceReady={Boolean(store.workspaceDir)}
                onOpenWorkspaceSettings={() => openSettingsTab('Workspace')}
              />
            ) : launchFailed ? (
              <CodeErrorView tabId={tab.id} retry={retry} />
            ) : (
              /* idle / checking / installing / ready / starting / connecting —
             at this point we already have a project or Routine session
             workspace (we passed the early return above), so auto-launch will
             drive the machine to ``running`` shortly. */
              <div className="w-full h-full flex items-center justify-center">
                <motion.div
                  className="inline-flex items-center gap-2 rounded-full bg-card pl-5 pr-5 pt-2 pb-2"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.3, ease: 'easeOut' }}
                >
                  <Spinner className="text-muted-foreground" />
                  <span className="text-sm text-muted-foreground">
                    {phase === 'idle' ? 'Restarting agent…' : 'Connecting…'}
                  </span>
                </motion.div>
              </div>
            )}
          </div>
        </ConversationComposerProvider>
      </VoiceScopeContext.Provider>
    );
  }
);
CodeTabContent.displayName = 'CodeTabContent';
