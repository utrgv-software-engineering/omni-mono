import { type ChatHost, ChatHostProvider } from '@omniagents/chat-ui/host'
import { RPCClientProvider } from '@omniagents/chat-ui/rpc-context'
import { UiConfigProvider } from '@omniagents/chat-ui/ui-config'
import { TooltipProvider } from '@omniagents/ui/tooltip'
import { type ReactNode, useMemo } from 'react'

import { api } from '../../lib/api'
import { useToastStore } from '../../stores/toastStore'

function useChatHost(): ChatHost {
  const pushToast = useToastStore((state) => state.pushToast)
  return useMemo(
    () => ({
      notifyError: (title, description) => {
        pushToast({ message: description ? `${title}: ${description}` : title, tone: 'error' })
      },
      // Whether the runtime offers realtime voice is the runtime's call.
      voiceMode: 'hosted',
      audioSettings: () => ({
        inputDeviceId: null,
        outputDeviceId: null,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      }),
    }),
    [pushToast],
  )
}

/**
 * A connection to the agent's real runtime, which Studio mounts at
 * `/api/agents/<key>/runtime`, for the shared chat UI and the shared
 * client inside it. Key it by agent: each agent is its own runtime.
 */
export function AgentRuntimeProvider({ agentKey, children }: { agentKey: string; children: ReactNode }) {
  const host = useChatHost()
  const connection = useMemo(() => api.agentRuntime(agentKey), [agentKey])
  return (
    <ChatHostProvider host={host}>
      <TooltipProvider delayDuration={300}>
        <UiConfigProvider connection={connection}>
          <RPCClientProvider>{children}</RPCClientProvider>
        </UiConfigProvider>
      </TooltipProvider>
    </ChatHostProvider>
  )
}
