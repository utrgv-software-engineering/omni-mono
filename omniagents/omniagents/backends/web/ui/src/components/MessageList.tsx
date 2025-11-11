import React, { useMemo, useState } from 'react'
import { MarkdownMessage } from '../shared/MarkdownMessage'

export type ChatMessage = {
  type: 'chat'
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp?: string
}

export type ToolItem = {
  type: 'tool'
  call_id?: string
  tool: string
  input?: string
  output?: string
  status: 'called' | 'result'
  metadata?: any
}

export type ApprovalItem = {
  type: 'approval'
  request_id: string
  tool: string
  argumentsText?: string
  metadata?: any
}

export type MessageItem = ChatMessage | ToolItem | ApprovalItem

export function MessageList({ items, statusText, thinking, onApprovalDecision }:
  { items: MessageItem[]; statusText?: string; thinking?: boolean; onApprovalDecision?: (request_id: string, value: 'yes' | 'always' | 'no') => void }) {
  return (
    <div className="flex-1 overflow-y-auto py-3">
      <div className="container-chat px-3 space-y-3 md:space-y-4">
        {items.length === 0 ? (
          <div className="text-sm text-textSubtle">Start by typing a message below.</div>
        ) : (
          items.map((m, i) => {
            if (m.type === 'chat') return <MessageBubble key={i} role={m.role}><MarkdownMessage content={m.content} variant={m.role} /></MessageBubble>
            if (m.type === 'tool') return <ToolCard key={m.call_id || i} item={m} />
            if (m.type === 'approval') return <ApprovalCard key={m.request_id} item={m} onDecision={onApprovalDecision} />
            return null
          })
        )}
        {(thinking || statusText) ? (
          <StatusRow text={statusText || (thinking ? 'Thinking…' : '')} spinner={!!thinking} />
        ) : null}
      </div>
    </div>
  )
}

function MessageBubble({ role, children }: { role: ChatMessage['role']; children: React.ReactNode }) {
  if (role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[70%] rounded-[18px] bg-tweetBlue text-white px-4 py-2">
          {children}
        </div>
      </div>
    )
  }
  if (role === 'system') {
    return (
      <div className="flex justify-start">
        <div className="max-w-[70%] rounded-[8px] bg-bgCardAlt text-gray-100 px-3 py-2 border border-bgCardAlt">
          {children}
        </div>
      </div>
    )
  }
  return (
    <div className="flex justify-start">
      <div className="max-w-[70%] px-4 py-2">
        {children}
      </div>
    </div>
  )
}

function StatusRow({ text, spinner }: { text?: string; spinner?: boolean }) {
  if (!text) return null
  return (
    <div className="flex items-center gap-2 text-xs text-textSubtle">
      {spinner ? <span className="inline-block w-[14px] h-[14px] border-2 border-t-transparent border-tweetBlue rounded-full animate-spin" /> : <span className="inline-block w-[14px] h-[14px]" />}
      <span>{text}</span>
    </div>
  )
}

function ToolCard({ item }: { item: ToolItem }) {
  const [open, setOpen] = useState(false)
  const hasResult = !!item.output && item.output.trim().length > 0
  const iconFill = hasResult ? 'bg-successGreen' : 'bg-warningOrange'
  const header = useMemo(() => {
    const meta = item.metadata
    const sum = meta && typeof meta === 'object' ? String(meta.summary || '') : ''
    if (sum && sum.trim().length > 0) return sum.trim()
    return buildToolHeader(item.tool, item.input)
  }, [item.tool, item.input, item.metadata])
  const title = hasResult ? header : `${header} • running…`
  return (
    <div className="rounded-md border border-bgCardAlt bg-bgCardAlt p-3">
      <div className="flex items-center gap-2">
        <span className={["inline-block w-2 h-2 rounded-full flex-shrink-0", iconFill].join(' ')} />
        <div className="flex-1 text-sm text-gray-100">{title}</div>
        <button
          className="w-6 h-6 flex items-center justify-center rounded hover:bg-bgColumn text-textSubtle hover:text-gray-200"
          aria-label="Toggle details"
          onClick={() => setOpen(v => !v)}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" className={[open ? 'rotate-180' : '', 'transition-transform'].join(' ')}>
            <path d="M7 10l5 5 5-5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>
      {open ? (
        <div className="mt-2 space-y-3">
          {renderToolBody(item)}
        </div>
      ) : null}
    </div>
  )
}

function ApprovalCard({ item, onDecision }: { item: ApprovalItem; onDecision?: (request_id: string, value: 'yes' | 'always' | 'no') => void }) {
  return (
    <div className="rounded-md border border-warningOrange bg-bgCardAlt p-3">
      <div className="text-sm font-semibold text-warningOrange">Approve {item.tool}</div>
      {item.argumentsText ? (
        <pre className="mt-2 text-xs overflow-x-auto whitespace-pre-wrap bg-bgColumn rounded p-2 border border-bgCardAlt text-gray-100">{item.argumentsText}</pre>
      ) : (
        <div className="text-xs text-textSubtle mt-1">No parameters</div>
      )}
      <div className="mt-3 flex gap-2 justify-end">
        <button className="px-3 py-1.5 text-xs rounded-md bg-errorRed hover:brightness-110 text-white" onClick={() => onDecision && onDecision(item.request_id, 'no')}>Reject</button>
        <button className="px-3 py-1.5 text-xs rounded-md bg-tweetBlue hover:brightness-110 text-white" onClick={() => onDecision && onDecision(item.request_id, 'always')}>Always</button>
        <button className="px-3 py-1.5 text-xs rounded-md bg-successGreen hover:brightness-110 text-white" onClick={() => onDecision && onDecision(item.request_id, 'yes')}>Approve Once</button>
      </div>
    </div>
  )
}

function buildToolHeader(tool: string, args?: string) {
  const preview = formatArgsPreview(args || '', 80)
  if (!preview) return tool
  return `${tool}(${preview})`
}

function formatArgsPreview(args: string, maxLen: number) {
  if (!args) return ''
  let parsed: any
  try { parsed = JSON.parse(args) } catch {}
  let text: string
  if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
    const parts: string[] = []
    Object.entries(parsed).forEach(([k, v]) => {
      let vs: string
      if (typeof v === 'string') vs = `"${v}"`
      else if (typeof v === 'number' || typeof v === 'boolean') vs = String(v)
      else {
        try { vs = JSON.stringify(v) } catch { vs = String(v) }
      }
      parts.push(`${k}: ${vs}`)
    })
    text = parts.join(', ')
  } else {
    text = args.replace(/\s+/g, ' ').trim()
  }
  if (text.length > maxLen) return text.slice(0, maxLen - 3) + '...'
  return text
}

function renderToolBody(item: ToolItem) {
  const sections: React.ReactNode[] = []
  const meta = item.metadata
  const out = item.output || ''
  const hasOut = out.trim().length > 0
  if (hasOut) {
    if (meta && typeof meta === 'object' && meta.display_type) {
      const dt = meta.display_type as string
      if (dt === 'diff') sections.push(diffView(linesFromDiff(meta)))
      else if (dt === 'table') sections.push(tableView(meta))
      else if (dt === 'file_write') sections.push(codeBlock((meta.value as string) || out))
      else if (dt === 'command') sections.push(commandView(meta, out))
      else if (typeof meta.preview === 'string' && meta.preview.trim().length > 0) sections.push(preText(meta.preview))
      else sections.push(preText(out))
    } else {
      sections.push(preText(out))
    }
  }
  return sections.length ? <div className="space-y-3">{sections}</div> : null
}

function linesFromDiff(metadata: any): string[] {
  const v = metadata?.value
  const acc: string[] = []
  if (v && typeof v === 'object' && Array.isArray(v.diff_lines)) {
    for (const s of v.diff_lines) if (typeof s === 'string') acc.push(s)
  }
  return acc
}

function diffView(lines: string[]) {
  const rows = lines.map((l, i) => {
    const cls = l.startsWith('+') && !l.startsWith('+++') ? 'text-successGreen' : (l.startsWith('-') && !l.startsWith('---') ? 'text-errorRed' : 'text-textSubtle')
    return <div key={i} className={[cls, 'font-mono text-[12px]'].join(' ')}>{l}</div>
  })
  return (
    <div className="w-full p-2 rounded border border-bgCardAlt bg-bgColumn">
      <div className="space-y-0.5">{rows}</div>
    </div>
  )
}

function tableView(metadata: any) {
  const t = metadata?.table || {}
  const cols: string[] = Array.isArray(t.columns) ? t.columns.map((c: any) => (c && typeof c === 'object' ? c.title : '')).filter(Boolean) : []
  const rows: string[][] = Array.isArray(t.rows) ? t.rows.map((r: any) => Array.isArray(r) ? r.map((e: any) => (e == null ? '' : String(e))) : []).filter((r: any) => r.length) : []
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-xs">
        <thead>
          <tr>
            {cols.map((c, i) => <th key={i} className="text-left font-semibold pr-4 pb-1">{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="align-top">
              {r.map((cell, j) => <td key={j} className="pr-4 py-1 whitespace-nowrap">{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function codeBlock(content: string) {
  return (
    <div className="w-full p-2 rounded border border-bgCardAlt bg-bgColumn">
      <pre className="whitespace-pre-wrap overflow-x-auto text-[12px] text-gray-100"><code>{content}</code></pre>
    </div>
  )
}

function preText(content: string) {
  return (
    <div className="w-full p-2 rounded border border-bgCardAlt bg-bgColumn">
      <pre className="whitespace-pre-wrap overflow-x-auto text-[12px] text-gray-100"><code>{content}</code></pre>
    </div>
  )
}

function commandView(metadata: any, plainOutput: string) {
  const meta = metadata?.metadata || {}
  let command = plainOutput || ''
  if (typeof meta?.command === 'string') command = meta.command
  let output = ''
  const value = metadata?.value
  if (typeof value === 'string' && value.trim().length > 0) output = value
  else if (typeof metadata?.preview === 'string' && metadata.preview.trim().length > 0) output = metadata.preview
  else {
    const stdout = meta?.stdout
    const stderr = meta?.stderr
    const parts: string[] = []
    if (typeof stdout === 'string' && stdout.trim().length > 0) parts.push(stdout)
    if (typeof stderr === 'string' && stderr.trim().length > 0) parts.push('[stderr]\n' + stderr)
    if (parts.length) output = parts.join('\n')
    if (!output && plainOutput && plainOutput.trim().length > 0) output = plainOutput
  }
  const status: string[] = []
  if (typeof meta?.success === 'boolean') status.push(meta.success ? 'success' : 'failed')
  if (typeof meta?.exit_code === 'number') status.push('exit ' + String(meta.exit_code))
  if (typeof meta?.has_stderr === 'boolean' && meta.has_stderr) status.push('stderr captured')
  return (
    <div className="space-y-2">
      <div className="w-full p-2 rounded border border-bgCardAlt bg-bgColumn font-mono text-[12px]">$ {String(command)}</div>
      {status.length ? <div className="text-[12px] text-textSubtle">{status.join(' • ')}</div> : null}
      {output.trim().length ? preText(output.trimEnd()) : null}
    </div>
  )
}
