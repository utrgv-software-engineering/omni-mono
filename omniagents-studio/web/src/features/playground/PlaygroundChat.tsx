import { BrowserChat } from '@omniagents/chat-ui/browser-chat'
import { type ChatHost, ChatHostProvider } from '@omniagents/chat-ui/host'
import { RPCClientProvider } from '@omniagents/chat-ui/rpc-context'
import { UiConfigProvider } from '@omniagents/chat-ui/ui-config'
import { TooltipProvider } from '@omniagents/ui/tooltip'
import { useMemo } from 'react'

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

interface PlaygroundChatProps {
  agentKey: string
  sessionId: string
  onSessionChange: (sessionId: string | undefined) => void
}

/**
 * The shared chat column (the same one the desktop app and the omniagents
 * web UI show) against the agent's real runtime, which Studio mounts at
 * `/api/agents/<key>/runtime`. Its conversations are recorded in the
 * canonical store like every other client's.
 */
export function PlaygroundChat({ agentKey, sessionId, onSessionChange }: PlaygroundChatProps) {
  const host = useChatHost()
  const connection = useMemo(() => api.agentRuntime(agentKey), [agentKey])
  return (
    <ChatHostProvider host={host}>
      <TooltipProvider delayDuration={300}>
        <UiConfigProvider connection={connection}>
          <RPCClientProvider>
            <BrowserChat sessionId={sessionId} onSessionChange={onSessionChange} />
          </RPCClientProvider>
        </UiConfigProvider>
      </TooltipProvider>
    </ChatHostProvider>
  )
}
