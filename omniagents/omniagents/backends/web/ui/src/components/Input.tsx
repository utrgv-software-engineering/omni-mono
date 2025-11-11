import React, { useState } from 'react'

export function Input({ disabled, onSubmit }: { disabled?: boolean; onSubmit: (text: string) => void }) {
  const [text, setText] = useState('')
  return (
    <div className="px-4 py-3 border-t border-gray-800 bg-gray-900/60">
      <form onSubmit={(e) => { e.preventDefault(); if (text.trim()) { onSubmit(text.trim()); setText('') } }}>
        <textarea
          className="w-full h-24 resize-y rounded-md bg-gray-900 border border-gray-700 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          placeholder="Type your message..."
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={disabled}
        />
        <div className="mt-2 flex justify-end">
          <button type="submit" disabled={disabled || !text.trim()} className="px-3 py-1.5 text-sm rounded-md bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700">
            Send
          </button>
        </div>
      </form>
    </div>
  )
}

