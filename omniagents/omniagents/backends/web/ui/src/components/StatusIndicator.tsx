import React from 'react'

export function StatusIndicator({ connected, thinking, status }: { connected: boolean; thinking: boolean; status?: string }) {
  return (
    <div className="px-4 py-2 text-xs text-gray-400">
      <span className={connected ? 'text-green-400' : 'text-red-400'}>
        {connected ? 'Connected' : 'Disconnected'}
      </span>
      {thinking ? <span className="ml-2 text-blue-400">Thinking…</span> : null}
      {status ? <span className="ml-2">{status}</span> : null}
    </div>
  )
}

