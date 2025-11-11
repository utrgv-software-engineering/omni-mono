import React, { useState } from 'react'

export function Input({ disabled, onSubmit }: { disabled?: boolean; onSubmit: (text: string) => void }) {
  const [text, setText] = useState('')
  return (
    <div className="px-4 py-3 border-t border-bgCardAlt bg-bgColumn">
      <form onSubmit={(e) => { e.preventDefault(); if (text.trim()) { onSubmit(text.trim()); setText('') } }}>
        <textarea
          className="w-full min-h-[80px] resize-y rounded-[24px] bg-bgCardAlt border-0 px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-tweetBlue placeholder:text-textSubtle text-white"
          placeholder="Type your message..."
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={disabled}
        />
        <div className="mt-2 flex justify-end">
          <button type="submit" disabled={disabled || !text.trim()} className="px-3 py-1.5 text-sm rounded-md bg-tweetBlue hover:brightness-110 disabled:bg-bgCardAlt disabled:text-textSubtle">
            Send
          </button>
        </div>
      </form>
    </div>
  )
}
