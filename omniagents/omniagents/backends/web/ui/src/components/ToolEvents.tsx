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
      <div className="text-xs text-textSubtle mb-2">Tool activity</div>
      <div className="space-y-2">
        {events.map((e, i) => (
          <div key={i} className="rounded-md border border-bgCardAlt bg-bgCardAlt p-3">
            <div className="text-xs flex items-center gap-2">
              <span className="inline-block w-5 h-5 rounded-full border" style={{ borderColor: e.status === 'result' ? '#17BF63' : '#FFA534' }} />
              <span className="text-gray-100">{e.tool}</span>
              <span className={e.status === 'result' ? 'text-successGreen' : 'text-warningOrange'}>{e.status}</span>
            </div>
            {e.input ? (
              <pre className="mt-2 text-[11px] text-gray-200 whitespace-pre-wrap overflow-x-auto bg-bgColumn rounded p-2 border border-bgCardAlt">{e.input}</pre>
            ) : null}
            {e.output ? (
              <pre className="mt-2 text-[11px] text-gray-100 whitespace-pre-wrap overflow-x-auto bg-bgColumn rounded p-2 border border-bgCardAlt">{e.output}</pre>
            ) : null}
            {e.metadata ? (
              <details className="mt-2">
                <summary className="text-[11px] text-textSubtle cursor-pointer">Metadata</summary>
                <pre className="mt-1 text-[11px] text-gray-100 whitespace-pre-wrap overflow-x-auto bg-bgColumn rounded p-2 border border-bgCardAlt">{JSON.stringify(e.metadata, null, 2)}</pre>
              </details>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  )
}
