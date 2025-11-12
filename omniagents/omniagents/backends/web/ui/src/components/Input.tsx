import React, { useCallback, useMemo, useRef, useState } from 'react'
import { PromptInput, PromptInputTextarea, PromptInputActions } from './promptkit/PromptInput'
import { VoiceModal } from './VoiceModal'

export function Input({ disabled, thinking, onStop, onSubmit }:
  { disabled?: boolean; thinking?: boolean; onStop?: () => void; onSubmit: (text: string, files?: File[]) => void }) {
  const [text, setText] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [history, setHistory] = useState<string[]>([])
  const [historyIndex, setHistoryIndex] = useState(0)
  const [historyDraft, setHistoryDraft] = useState('')
  const [isVoiceModalOpen, setIsVoiceModalOpen] = useState(false)
  const taRef = useRef<HTMLTextAreaElement | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const canSend = useMemo(() => !disabled && (text.trim().length > 0 || files.length > 0), [disabled, text, files])

  const insertNewlineAtCursor = useCallback((el?: HTMLTextAreaElement) => {
    const target = el ?? taRef.current
    if (!target) return
    const start = target.selectionStart ?? text.length
    const end = target.selectionEnd ?? text.length
    const next = text.slice(0, start) + '\n' + text.slice(end)
    setText(next)
    const pos = start + 1
    requestAnimationFrame(() => {
      try { target.setSelectionRange(pos, pos) } catch {}
    })
  }, [text])

  const handleSubmit = useCallback(() => {
    const t = text.trim()
    if (!t && files.length === 0) return
    onSubmit(t, files)
    setHistory(h => h.length && h[h.length - 1] === t ? h : [...h, t])
    setHistoryIndex(0)
    setHistoryDraft('')
    setText('')
    setFiles([])
  }, [text, files, onSubmit])

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Escape') {
      e.preventDefault()
      if (thinking && onStop) {
        onStop()
      }
      return
    }
    if (e.key === 'Enter') {
      if (e.shiftKey || e.altKey) {
        e.preventDefault()
        insertNewlineAtCursor(e.currentTarget)
        return
      }
    }
    if ((e.ctrlKey || e.metaKey) && (e.key.toLowerCase() === 'j')) {
      e.preventDefault()
      insertNewlineAtCursor(e.currentTarget)
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
  }, [text, history, historyIndex, historyDraft, handleSubmit, insertNewlineAtCursor, thinking, onStop])

  const openFilePicker = useCallback(() => {
    fileInputRef.current?.click()
  }, [])

  const handleFilesSelected = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const list = e.target.files ? Array.from(e.target.files) : []
    setFiles(list)
  }, [])

  return (
    <div>
      <div className="container-chat px-3 py-3">
        <PromptInput
          isLoading={!!thinking}
          value={text}
          onValueChange={setText}
          onSubmit={handleSubmit}
          disabled={disabled}
          className=""
        >
          {files.length > 0 && (
            <div className="flex flex-wrap gap-2 pb-2 px-2" onClick={(e) => e.stopPropagation()}>
              {files.map((f, i) => (
                <div key={i} className="bg-bgCard flex items-center gap-2 rounded-lg px-3 py-2 text-sm">
                  <svg width="16" height="16" viewBox="0 0 24 24" className="text-white" aria-hidden="true">
                    <path d="M21.44 11.05l-8.49 8.49a5.5 5.5 0 11-7.78-7.78l8.49-8.49a3.5 3.5 0 114.95 4.95l-8.49 8.49a1.5 1.5 0 11-2.12-2.12l8.49-8.49" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                  <span className="max-w-[120px] truncate text-white" title={f.name}>{f.name}</span>
                  <button onClick={() => {
                    setFiles(prev => prev.filter((_, idx) => idx !== i))
                    if (fileInputRef.current) fileInputRef.current.value = ''
                  }} className="hover:bg-bgCardAlt rounded-full p-1">
                    <svg width="16" height="16" viewBox="0 0 24 24" className="text-white" aria-hidden="true">
                      <path d="M18 6L6 18M6 6l12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                  </button>
                </div>
              ))}
            </div>
          )}

          <PromptInputTextarea
            placeholder="Type a message…"
            onKeyDown={handleKeyDown}
            className="max-h-[50vh]"
            disabled={disabled}
          />

          <PromptInputActions className="flex items-center justify-between gap-2 pt-2 px-2">
            <label
              htmlFor="file-upload"
              onClick={(e) => e.stopPropagation()}
              className="hover:bg-bgCardAlt/50 flex h-8 w-8 cursor-pointer items-center justify-center rounded-2xl"
              aria-label="Attach files"
            >
              <input
                ref={fileInputRef}
                type="file"
                multiple
                onChange={handleFilesSelected}
                className="hidden"
                id="file-upload"
              />
              <svg width="20" height="20" viewBox="0 0 24 24" className="text-white" aria-hidden="true">
                <path d="M21.44 11.05l-8.49 8.49a5.5 5.5 0 11-7.78-7.78l8.49-8.49a3.5 3.5 0 114.95 4.95l-8.49 8.49a1.5 1.5 0 11-2.12-2.12l8.49-8.49" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </label>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setIsVoiceModalOpen(true)}
                className="hover:bg-bgCardAlt/50 flex h-8 w-8 items-center justify-center rounded-2xl"
                aria-label="Voice mode"
              >
                <svg width="20" height="20" viewBox="0 0 24 24" className="text-white" aria-hidden="true">
                  <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" fill="currentColor"/>
                  <path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" fill="none"/>
                </svg>
              </button>

              {!thinking ? (
              <button
                type="button"
                disabled={!canSend}
                onClick={handleSubmit}
                className="h-8 w-8 rounded-full bg-tweetBlue text-white flex items-center justify-center disabled:bg-bgCardAlt disabled:text-textSubtle hover:brightness-110 focus:outline-none shadow-sm"
                aria-label="Send"
                title="Send (Enter)"
              >
                <svg width="20" height="20" viewBox="0 0 24 24" className="pointer-events-none" aria-hidden="true">
                  <path d="M12 19V5M5 12l7-7 7 7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
            ) : (
              <button
                type="button"
                onClick={onStop}
                className="h-8 w-8 rounded-full bg-errorRed text-white flex items-center justify-center hover:brightness-110 focus:outline-none shadow-sm"
                aria-label="Stop"
                title="Stop"
              >
                <svg width="20" height="20" viewBox="0 0 24 24" className="pointer-events-none" aria-hidden="true">
                  <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" />
                </svg>
              </button>
              )}
            </div>
          </PromptInputActions>
        </PromptInput>
      </div>

      <VoiceModal isOpen={isVoiceModalOpen} onClose={() => setIsVoiceModalOpen(false)} />
    </div>
  )
}
