import type { RunOverrides } from '@omniagents/client/rpc/execution';
import type { ComponentType, ReactNode } from 'react';
import { createContext, useContext } from 'react';

/**
 * What the app embedding the chat UI provides: the services that differ
 * between the desktop, the web UI and Studio. Everything else the chat UI
 * does itself, against the agent runtime.
 */
export type ChatHost = {
  /** Shows a transient error notice. */
  notifyError(title: string, description?: string): void;
  /**
   * How spoken input works here: the host's own local transcription
   * (`LocalVoiceButton`), the runtime's hosted realtime voice, or none.
   */
  voiceMode: VoiceMode;
  /** Push-to-talk for `voiceMode: 'local'`; submits the transcript. */
  LocalVoiceButton?: ComponentType<{ onSubmit: (text: string) => void }>;
  /** Microphone and speaker choice for hosted realtime voice. */
  audioSettings(): AudioSettings;
  /**
   * Scheduled tasks: a column opened for a routine run registers an actor the
   * host drives, and reports the run's lifecycle back.
   */
  routines?: {
    register(actor: RoutineActor): () => void;
    forward(event: RoutineEvent): void;
  };
};

export type VoiceMode = 'local' | 'hosted' | 'off';

export type AudioSettings = {
  /** mediaDevices deviceId for the input mic. Null = OS default. */
  inputDeviceId: string | null;
  /** mediaDevices deviceId for output (applied via setSinkId on a routed <audio> element). Null = OS default. */
  outputDeviceId: string | null;
  echoCancellation: boolean;
  noiseSuppression: boolean;
  autoGainControl: boolean;
};

export type RoutineActor = {
  taskId: string;
  /**
   * Start a single run on the column's session through the same submit path
   * the user's keyboard uses. `safeToolOverrides` carries the routine's
   * always-allow list. Resolves with the runId once `start_run` is acked.
   */
  startRun: (prompt: string, safeToolOverrides?: RunOverrides['safeToolOverrides']) => Promise<{ runId: string }>;
  /** Stop any in-flight run on the column's session. */
  stop: () => Promise<void>;
};

export type RoutineApprovalRequest = {
  kind: 'function' | 'mcp';
  toolName?: string;
  serverLabel?: string;
};

export type RoutineEvent =
  | { kind: 'run-started'; taskId: string; runId: string }
  | { kind: 'run-end'; taskId: string; reason: string }
  | { kind: 'approval-requested'; taskId: string; approval: RoutineApprovalRequest }
  | { kind: 'approval-resolved'; taskId: string }
  | { kind: 'disconnected'; taskId: string };

const ChatHostContext = createContext<ChatHost | null>(null);

export function ChatHostProvider({ host, children }: { host: ChatHost; children: ReactNode }) {
  return <ChatHostContext.Provider value={host}>{children}</ChatHostContext.Provider>;
}

export function useChatHost(): ChatHost {
  const host = useContext(ChatHostContext);
  if (!host) {
    throw new Error('ChatHostProvider is missing');
  }
  return host;
}
