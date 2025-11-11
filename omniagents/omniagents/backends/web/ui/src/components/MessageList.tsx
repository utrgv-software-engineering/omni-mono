import React from 'react'
import { MarkdownMessage } from '../shared/MarkdownMessage'

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp?: string
}

export function MessageList({ messages }: { messages: ChatMessage[] }) {
  return (
    <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
      {messages.length === 0 ? (
        <div className="text-sm text-gray-500">Start by typing a message below.</div>
      ) : (
        messages.map((m, i) => (
          <div key={i} className={m.role === 'user' ? 'text-blue-200' : ''}>
            <MarkdownMessage content={m.content} variant={m.role} />
          </div>
        ))
      )}
    </div>
  )
}

