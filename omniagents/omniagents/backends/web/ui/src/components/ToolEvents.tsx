import React from 'react'

type ToolEvent = {
  call_id?: string
  tool: string
  input?: string
  output?: string
  status: 'called' | 'result'
  metadata?: any
}

export function ToolEvents({ events }: { events: ToolEvent[] }) {
  if (!events.length) return null
  return (
    <div className="px-4 py-3">
      <div className="text-xs text-gray-400 mb-2">Tool activity</div>
      <div className="space-y-2">
        {events.map((e, i) => (
          <div key={i} className="rounded-md border border-gray-700 bg-gray-900 p-2">
            <div className="text-xs"><span className="text-gray-300">{e.tool}</span> · <span className={e.status === 'result' ? 'text-green-400' : 'text-yellow-300'}>{e.status}</span></div>
            {e.input ? (
              <pre className="mt-1 text-[11px] text-gray-400 whitespace-pre-wrap overflow-x-auto">{e.input}</pre>
            ) : null}
            {e.output ? (
              <pre className="mt-1 text-[11px] text-gray-200 whitespace-pre-wrap overflow-x-auto">{e.output}</pre>
            ) : null}
            {e.metadata ? (
              <details className="mt-1">
                <summary className="text-[11px] text-gray-400 cursor-pointer">Metadata</summary>
                <pre className="mt-1 text-[11px] text-gray-300 whitespace-pre-wrap overflow-x-auto">{JSON.stringify(e.metadata, null, 2)}</pre>
              </details>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  )
}
