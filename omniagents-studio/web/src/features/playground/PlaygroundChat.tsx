import { BrowserChat } from '@omniagents/chat-ui/browser-chat'

import { AgentRuntimeProvider } from '../runtime/AgentRuntimeProvider'

interface PlaygroundChatProps {
  agentKey: string
  sessionId: string
  onSessionChange: (sessionId: string | undefined) => void
}

/**
 * The shared chat column (the same one the desktop app and the omniagents
 * web UI show) against the agent's real runtime. Its conversations are
 * recorded in the canonical store like every other client's.
 */
export function PlaygroundChat({ agentKey, sessionId, onSessionChange }: PlaygroundChatProps) {
  return (
    <AgentRuntimeProvider agentKey={agentKey}>
      <BrowserChat sessionId={sessionId} onSessionChange={onSessionChange} />
    </AgentRuntimeProvider>
  )
}
