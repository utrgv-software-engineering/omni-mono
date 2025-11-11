import React, { useCallback, useMemo, useRef, useState } from 'react'

export function Input({ disabled, thinking, onStop, onSubmit }:
  { disabled?: boolean; thinking?: boolean; onStop?: () => void; onSubmit: (text: string) => void }) {
  const [text, setText] = useState('')
  const [history, setHistory] = useState<string[]>([])
  const [historyIndex, setHistoryIndex] = useState(0)
  const [historyDraft, setHistoryDraft] = useState('')
  const taRef = useRef<HTMLTextAreaElement | null>(null)

  const canSend = useMemo(() => !disabled && text.trim().length > 0, [disabled, text])

  const insertNewlineAtCursor = useCallback(() => {
    const el = taRef.current
    if (!el) return
    const start = el.selectionStart ?? text.length
    const end = el.selectionEnd ?? text.length
    const next = text.slice(0, start) + '\n' + text.slice(end)
    setText(next)
    const pos = start + 1
    requestAnimationFrame(() => {
      try { el.setSelectionRange(pos, pos) } catch {}
    })
  }, [text])

  const handleSubmit = useCallback(() => {
    const t = text.trim()
    if (!t) return
    onSubmit(t)
    setHistory(h => h.length && h[h.length - 1] === t ? h : [...h, t])
    setHistoryIndex(0)
    setHistoryDraft('')
    setText('')
  }, [text, onSubmit])

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter') {
      if (e.shiftKey || e.altKey) {
        e.preventDefault()
        insertNewlineAtCursor()
        return
      }
      if (!e.ctrlKey && !e.metaKey) {
        e.preventDefault()
        handleSubmit()
        return
      }
    }
    if ((e.ctrlKey || e.metaKey) && (e.key.toLowerCase() === 'j')) {
      e.preventDefault()
      insertNewlineAtCursor()
      return
    }
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
      const el = e.currentTarget
      const caretAtStart = (el.selectionStart ?? 0) === 0 && (el.selectionEnd ?? 0) === 0
      const caretAtEnd = (el.selectionStart ?? 0) === text.length && (el.selectionEnd ?? 0) === text.length
      if (e.key === 'ArrowUp' && caretAtStart) {
        e.preventDefault()
        if (historyIndex === 0) setHistoryDraft(text)
        const nextIndex = Math.min(history.length, historyIndex + 1)
        setHistoryIndex(nextIndex)
        const replacement = nextIndex > 0 ? history[history.length - nextIndex] : historyDraft
        if (replacement != null) setText(replacement)
        requestAnimationFrame(() => {
          try { el.setSelectionRange(0, 0) } catch {}
        })
      } else if (e.key === 'ArrowDown' && caretAtEnd) {
        e.preventDefault()
        const nextIndex = Math.max(0, historyIndex - 1)
        setHistoryIndex(nextIndex)
        const replacement = nextIndex > 0 ? history[history.length - nextIndex] : historyDraft
        if (replacement != null) setText(replacement)
        requestAnimationFrame(() => {
          const pos = (replacement ?? '').length
          try { el.setSelectionRange(pos, pos) } catch {}
        })
      }
    }
  }, [text, history, historyIndex, historyDraft, handleSubmit, insertNewlineAtCursor])

  return (
    <div className="border-t border-bgCardAlt bg-bgColumn">
      <div className="container-chat px-3 py-3">
        <form onSubmit={(e) => { e.preventDefault(); handleSubmit() }}>
          <div className="relative">
            <textarea
              ref={taRef}
              className="w-full min-h-[44px] max-h-[50vh] resize-none rounded-[24px] bg-bgCardAlt border-0 pr-16 pl-4 py-3 text-sm focus:outline-none focus:ring-0 placeholder:text-textSubtle text-white"
              placeholder="Type a message…"
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={disabled}
            />
            {!thinking ? (
              <button
                type="submit"
                disabled={!canSend}
                className="absolute right-2 top-1/2 -translate-y-1/2 w-9 h-9 rounded-full bg-tweetBlue text-white flex items-center justify-center disabled:bg-bgCardAlt disabled:text-textSubtle hover:brightness-110 focus:outline-none shadow-sm"
                aria-label="Send"
                title="Send (Enter)"
              >
                <svg width="18" height="18" viewBox="0 0 24 24" className="pointer-events-none" aria-hidden="true">
                  <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" fill="currentColor" />
                </svg>
              </button>
            ) : (
              <button
                type="button"
                onClick={onStop}
                className="absolute right-2 top-1/2 -translate-y-1/2 w-9 h-9 rounded-full bg-errorRed text-white flex items-center justify-center hover:brightness-110 focus:outline-none shadow-sm"
                aria-label="Stop"
                title="Stop"
              >
                <svg width="18" height="18" viewBox="0 0 24 24" className="pointer-events-none" aria-hidden="true">
                  <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" />
                </svg>
              </button>
            )}
          </div>
        </form>
      </div>
    </div>
  )
}
