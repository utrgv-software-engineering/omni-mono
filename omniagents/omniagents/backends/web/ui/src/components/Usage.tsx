import React from 'react'

type UsageDelta = {
  requests?: number
  input_tokens?: number
  output_tokens?: number
  total_tokens?: number
  input_tokens_details?: Record<string, number>
  output_tokens_details?: Record<string, number>
}

type UsageTotals = UsageDelta

export function Usage({ model, contextWindow, maxOutputTokens, delta, totals }: {
  model?: string
  contextWindow?: number
  maxOutputTokens?: number
  delta?: UsageDelta
  totals?: UsageTotals
}) {
  const fmt = (n?: number) => typeof n === 'number' ? n.toLocaleString() : '-'
  return (
    <div className="px-4 py-2 border-t border-gray-800 bg-gray-900/60 text-xs text-gray-300">
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        <span>Model: <span className="text-gray-100">{model || '-'}</span></span>
        <span>Context: <span className="text-gray-100">{fmt(contextWindow)}</span></span>
        <span>Max Output: <span className="text-gray-100">{fmt(maxOutputTokens)}</span></span>
      </div>
      <div className="mt-1 grid grid-cols-2 md:grid-cols-4 gap-2">
        <div className="bg-gray-800/60 rounded p-2">
          <div className="text-gray-400">Delta Input</div>
          <div className="text-gray-100">{fmt(delta?.input_tokens)}</div>
        </div>
        <div className="bg-gray-800/60 rounded p-2">
          <div className="text-gray-400">Delta Output</div>
          <div className="text-gray-100">{fmt(delta?.output_tokens)}</div>
        </div>
        <div className="bg-gray-800/60 rounded p-2">
          <div className="text-gray-400">Total Input</div>
          <div className="text-gray-100">{fmt(totals?.input_tokens)}</div>
        </div>
        <div className="bg-gray-800/60 rounded p-2">
          <div className="text-gray-400">Total Output</div>
          <div className="text-gray-100">{fmt(totals?.output_tokens)}</div>
        </div>
      </div>
    </div>
  )
}

