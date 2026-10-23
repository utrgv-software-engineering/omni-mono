import type { ClientToolCallHandler } from '@omniagents/chat-ui/App';
import { App as OmniAgentsCore } from '@omniagents/chat-ui/App';
import type { PendingMessage } from '@omniagents/chat-ui/ChatShell';
import { RPCClientProvider } from '@omniagents/chat-ui/rpc-context';
import type { SessionController } from '@omniagents/chat-ui/session-controller';
import { UiConfigProvider } from '@omniagents/chat-ui/ui-config';
import type { ExecutionTarget } from '@omniagents/client/rpc/execution';
import type { PlanItem } from '@omniagents/client/transcript/chat-types';
import type { ReactNode } from 'react';
import { useMemo } from 'react';

import { serverOrigin } from '@/renderer/services/ipc';
import type { AgentRuntimeConnection } from '@/shared/types';

type OmniAgentsAppProps = {
  /** RPC server connection for the directly embedded React client. */
  connection: AgentRuntimeConnection;
  sessionId?: string;
  /** Execution environment used by workspace, process, and terminal RPCs. */
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
  onPendingMessagesFlushed?: () => void;
  /** Extra composer chips (e.g. attach-project) rendered after the sandbox chip. */
  composerExtras?: ReactNode;
  onClientToolCall?: ClientToolCallHandler;
  /** Hands an imperative controller up so the global orchestrator can drive this column. */
  onController?: (controller: SessionController | null) => void;
  /** Fires when a run in this session ends — used to push column-done wakeups. */
  onRunEnd?: (info: { runId?: string; reason?: string }) => void;
  /** Fires when a run starts — used to pin a dispatched run id for the wakeup. */
  onRunStarted?: (runId: string) => void;
  pendingPlan?: PlanItem | null;
  onPlanDecision?: (approved: boolean) => void;
  /** Routine (scheduled task) bound to this column. When set, the app registers a routine bridge actor. */
  routineId?: string;
  /** Project workspace directory the launcher already knows about. Used to
   *  pre-fill the composer folder chip while the chat-boot RPC resolves.
   *  Session scratch dirs (UUID basename) render no chip. */
  workspaceDir?: string;
  /** Opens a column app (deck sidecar tab) — e.g. the Agents pill. */
  onOpenApp?: (appId: string) => void;
  /** Transcript-viewer mode: no composer or pill row. */
  readOnly?: boolean;
  /** Refetch a read-only transcript when its external worker advances. */
  transcriptRevision?: string;
  /** Additional column UI that must share this exact RPC connection. */
  providerChildren?: ReactNode;
};

export const OmniAgentsApp = ({
  connection,
  sessionId,
  executionTarget,
  onSessionChange,
  variables,
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
  providerChildren,
}: OmniAgentsAppProps) => {
  // Resolve relative ``/proxy/...`` payloads against the launcher's actual
  // origin — same-origin in browser server mode, backend baseUrl in
  // remote-linked Electron. An absolute connection base ignores the fallback.
  const normalizedConnection = useMemo<AgentRuntimeConnection>(
    () => ({ ...connection, baseUrl: new URL(connection.baseUrl, serverOrigin()).toString() }),
    [connection.authToken, connection.baseUrl]
  );

  return (
    <UiConfigProvider connection={normalizedConnection}>
      <RPCClientProvider>
        <OmniAgentsCore
          sessionId={sessionId}
          executionTarget={executionTarget}
          onSessionChange={onSessionChange}
          variables={variables}
          voiceVariables={voiceVariables}
          greeting={greeting}
          suggestions={suggestions}
          onReady={onReady}
          headerActionsTargetId={headerActionsTargetId}
          headerActionsCompact={headerActionsCompact}
          pendingMessages={pendingMessages}
          onPendingMessagesFlushed={onPendingMessagesFlushed}
          composerExtras={composerExtras}
          onClientToolCall={onClientToolCall}
          onController={onController}
          onRunEnd={onRunEnd}
          onRunStarted={onRunStarted}
          pendingPlan={pendingPlan}
          onPlanDecision={onPlanDecision}
          routineId={routineId}
          workspaceDir={workspaceDir}
          onOpenApp={onOpenApp}
          readOnly={readOnly}
          transcriptRevision={transcriptRevision}
        />
        {providerChildren}
      </RPCClientProvider>
    </UiConfigProvider>
  );
};
