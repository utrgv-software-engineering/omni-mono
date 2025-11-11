import React, { useMemo } from 'react'
import type { SessionItem } from './SessionList'

export function Sidebar({ open, sessions, selectedId, onClose, onNewChat, onSelect }:
  { open: boolean; sessions: SessionItem[]; selectedId?: string; onClose: () => void; onNewChat: () => void; onSelect: (id: string) => void }) {
  const ordered = useMemo(() => {
    const ts = (s: any) => {
      const t = s?.last_message?.timestamp || s?.created_at
      const n = Date.parse(String(t || ''))
      return isNaN(n) ? 0 : n
    }
    return sessions.slice().sort((a: any, b: any) => ts(b) - ts(a))
  }, [sessions])

  return (
    <>
      {/* Desktop/Tablet: Inline sidebar beside content */}
      {open && (
        <div className="hidden md:flex md:flex-col md:w-72 md:border-r md:border-bgCardAlt md:bg-bgColumn md:flex-shrink-0 md:h-full">
          <div className="p-2 border-b border-bgCardAlt flex items-center justify-between flex-shrink-0">
            <button className="px-3 py-2 rounded-md bg-tweetBlue text-white hover:brightness-110" onClick={onNewChat}>New Chat</button>
            <button className="w-8 h-8 rounded hover:bg-bgCardAlt text-gray-100" onClick={onClose} aria-label="Close">✕</button>
          </div>
          <div className="flex-1 p-2 space-y-2 overflow-y-auto min-h-0">
            {ordered.map(s => (
              <button
                key={s.id}
                onClick={() => onSelect(s.id)}
                className={[
                  'w-full text-left px-3 py-2 rounded-md border bg-bgCardAlt hover:brightness-110',
                  selectedId === s.id ? 'border-tweetBlue' : 'border-bgCardAlt',
                ].join(' ')}
              >
                <div className="text-sm font-medium text-gray-100 truncate">{s.id}</div>
                <div className="text-xs text-textSubtle">Messages: {s.message_count}</div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Mobile: Full overlay with backdrop */}
      {open && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={onClose} />
          <div className="absolute left-0 top-0 bottom-0 w-72 bg-bgColumn border-r border-bgCardAlt flex flex-col">
            <div className="flex items-center justify-between p-2 border-b border-bgCardAlt flex-shrink-0">
              <button className="px-3 py-2 rounded-md bg-tweetBlue text-white hover:brightness-110" onClick={onNewChat}>New Chat</button>
              <button className="w-8 h-8 rounded hover:bg-bgCardAlt text-gray-100" onClick={onClose} aria-label="Close">✕</button>
            </div>
            <div className="flex-1 p-2 space-y-2 overflow-y-auto min-h-0">
              {ordered.map(s => (
                <button
                  key={s.id}
                  onClick={() => { onSelect(s.id); onClose() }}
                  className={[
                    'w-full text-left px-3 py-2 rounded-md border bg-bgCardAlt hover:brightness-110',
                    selectedId === s.id ? 'border-tweetBlue' : 'border-bgCardAlt',
                  ].join(' ')}
                >
                  <div className="text-sm font-medium text-gray-100 truncate">{s.id}</div>
                  <div className="text-xs text-textSubtle">Messages: {s.message_count}</div>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  )
}

export default Sidebar
