import { useStore } from '@nanostores/react';
import { type ChatHost, ChatHostProvider } from '@omniagents/chat-ui/host';
import type { ReactNode } from 'react';
import { useMemo } from 'react';

import { configuredVoiceMode } from '@/lib/voice-mode';
import { toast } from '@/renderer/features/Toast/state';
import { LocalVoiceButton } from '@/renderer/omniagents-ui/components/LocalVoiceButton';
import { $modelsConfig } from '@/renderer/services/config';
import { forwardRoutineEvent, registerRoutineActor } from '@/renderer/services/routine-bridge';
import { persistedStoreApi } from '@/renderer/services/store';

/** The desktop's services for the chat UI: its toasts, local voice sidecar, audio
 *  settings and scheduled-task bridge. */
export function DesktopChatHost({ children }: { children: ReactNode }) {
  const localVoiceEnabled = useStore(persistedStoreApi.$atom).localVoiceEnabled;
  const voiceMode = configuredVoiceMode(localVoiceEnabled, useStore($modelsConfig));
  const host = useMemo<ChatHost>(
    () => ({
      notifyError: (title, description) => {
        toast.error(title, description);
      },
      voiceMode,
      LocalVoiceButton,
      audioSettings: () => persistedStoreApi.$atom.get().audioSettings,
      routines: { register: registerRoutineActor, forward: forwardRoutineEvent },
    }),
    [voiceMode]
  );
  return <ChatHostProvider host={host}>{children}</ChatHostProvider>;
}
