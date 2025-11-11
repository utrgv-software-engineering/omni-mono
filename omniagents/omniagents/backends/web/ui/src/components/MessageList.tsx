import React from 'react'
import { MarkdownMessage } from '../shared/MarkdownMessage'

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp?: string
}

export function MessageList({ messages }: { messages: ChatMessage[] }) {
  return (
    <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
      {messages.length === 0 ? (
        <div className="text-sm text-textSubtle">Start by typing a message below.</div>
      ) : (
        messages.map((m, i) => (
          <MessageBubble key={i} role={m.role}>
            <MarkdownMessage content={m.content} variant={m.role} />
          </MessageBubble>
        ))
      )}
    </div>
  )
}

function MessageBubble({ role, children }: { role: ChatMessage['role']; children: React.ReactNode }) {
  if (role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[404px] rounded-[18px] bg-tweetBlue text-white px-4 py-2">
          {children}
        </div>
      </div>
    )
  }
  if (role === 'system') {
    return (
      <div className="flex justify-start">
        <div className="max-w-[404px] rounded-[8px] bg-bgCardAlt text-gray-100 px-3 py-2 border border-bgCardAlt">
          {children}
        </div>
      </div>
    )
  }
  return (
    <div className="flex justify-start">
      <div className="max-w-[404px] px-4 py-2">
        {children}
      </div>
    </div>
  )
}
