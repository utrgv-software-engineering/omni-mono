import React from 'react'

export function ApprovalDialog({ tool, argumentsText, onDecision }: { tool: string; argumentsText?: string; onDecision: (value: 'yes' | 'always' | 'no') => void }) {
  return (
    <div className="mx-4 my-3 p-3 rounded-md border border-yellow-600 bg-yellow-900/20">
      <div className="text-sm font-semibold text-yellow-300">Tool Approval Required</div>
      <div className="text-xs text-yellow-200 mt-1">Tool: {tool}</div>
      {argumentsText ? <pre className="mt-2 text-xs overflow-x-auto whitespace-pre-wrap">{argumentsText}</pre> : null}
      <div className="mt-3 flex gap-2">
        <button className="px-3 py-1.5 text-xs rounded-md bg-green-600 hover:bg-green-500" onClick={() => onDecision('yes')}>Approve</button>
        <button className="px-3 py-1.5 text-xs rounded-md bg-blue-600 hover:bg-blue-500" onClick={() => onDecision('always')}>Always Approve</button>
        <button className="px-3 py-1.5 text-xs rounded-md bg-red-600 hover:bg-red-500" onClick={() => onDecision('no')}>Reject</button>
      </div>
    </div>
  )
}

