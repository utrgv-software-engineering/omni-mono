import React from 'react'

export type SessionItem = { id: string; created_at: string; archived: boolean; message_count: number; first_message?: any; last_message?: any }

export function SessionList({ sessions, onSelect }: { sessions: SessionItem[]; onSelect: (id?: string) => void }) {
  return (
    <div className="px-4 py-3">
      <div className="text-sm text-gray-400 mb-2">Resume a previous session or start a new one.</div>
      <div className="space-y-2">
        {sessions.map(s => (
          <button key={s.id} className="w-full text-left px-3 py-2 rounded-md bg-gray-900 border border-gray-700 hover:bg-gray-800" onClick={() => onSelect(s.id)}>
            <div className="text-sm font-medium">{s.id}</div>
            <div className="text-xs text-gray-400">Messages: {s.message_count} · Created: {s.created_at}</div>
          </button>
        ))}
        <button className="px-3 py-2 rounded-md bg-blue-600 hover:bg-blue-500" onClick={() => onSelect(undefined)}>Start New Session</button>
      </div>
    </div>
  )
}

