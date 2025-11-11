import React from 'react'

export function ApprovalDialog({ tool, argumentsText, onDecision }: { tool: string; argumentsText?: string; onDecision: (value: 'yes' | 'always' | 'no') => void }) {
  return (
    <div className="mx-4 my-3 p-3 rounded-md border border-warningOrange bg-bgCardAlt">
      <div className="text-sm font-semibold text-warningOrange">Tool Approval Required</div>
      <div className="text-xs text-textSubtle mt-1">Tool: {tool}</div>
      {argumentsText ? (
        <pre className="mt-2 text-xs overflow-x-auto whitespace-pre-wrap bg-bgColumn rounded p-2 border border-bgCardAlt text-gray-100">{argumentsText}</pre>
      ) : null}
      <div className="mt-3 flex gap-2">
        <button className="px-3 py-1.5 text-xs rounded-md bg-successGreen hover:brightness-110 text-white" onClick={() => onDecision('yes')}>Approve</button>
        <button className="px-3 py-1.5 text-xs rounded-md bg-tweetBlue hover:brightness-110 text-white" onClick={() => onDecision('always')}>Always Approve</button>
        <button className="px-3 py-1.5 text-xs rounded-md bg-errorRed hover:brightness-110 text-white" onClick={() => onDecision('no')}>Reject</button>
      </div>
    </div>
  )
}
