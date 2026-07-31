/**
 * State normalization for the canonical conversation domain objects.
 *
 * Before this module the transcript was rebuilt by folding the live
 * notification stream (`run_started` → `tool_called` → `tool_result` → …)
 * into `MessageItem[]`. That works while a socket is open and fails the
 * moment it isn't: reconnects replayed events, history came back as raw
 * Responses-API rows, and the two paths drifted.
 *
 * `normalizeItems` replaces the reconstruction with a projection. The
 * server has already resolved every item to one authoritative final state,
 * so rendering is a pure map from `ConversationItem[]` (ordered by `seq`)
 * onto the existing `MessageItem` union — no event folding, no ordering
 * heuristics, no "is this tool still running?" inference.
 */

import type { ConversationItem, ConversationToolKind } from '../rpc/client'
import type { MessageItem, ToolMetadata } from './MessageList'

/** Narrow an open `content` field to a string. */
function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined
}

/** Tool output/input are open JSON; render them as text without lying. */
function asText(value: unknown): string | undefined {
  if (value === undefined || value === null) return undefined
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

export const TERMINAL_ITEM_STATUSES = ['completed', 'failed', 'cancelled'] as const

export function isTerminal(item: ConversationItem): boolean {
  return (TERMINAL_ITEM_STATUSES as readonly string[]).includes(item.status)
}

/**
 * The tool taxonomy the server stamped onto a `tool_call` item. Present on
 * every recorded tool call; `other` when the server could not classify it.
 */
export function toolKind(item: ConversationItem): ConversationToolKind {
  const kind = item.content.tool_kind
  return typeof kind === 'string' ? (kind as ConversationToolKind) : 'other'
}

function normalizeOne(item: ConversationItem): MessageItem | null {
  switch (item.kind) {
    case 'user_message':
      return {
        type: 'chat',
        role: 'user',
        content: text(item.content.text),
      }
    case 'agent_message':
      return {
        type: 'chat',
        role: item.role === 'system' ? 'system' : 'assistant',
        content: text(item.content.text),
      }
    case 'compaction':
      return {
        type: 'chat',
        role: 'system',
        content: text(item.content.summary),
      }
    case 'tool_call':
      return {
        type: 'tool',
        call_id:
          typeof item.content.call_id === 'string' ? item.content.call_id : item.item_id,
        tool: text(item.content.tool) || 'tool',
        input: asText(item.content.input),
        output: asText(item.content.output),
        // A tool item that has NOT reached a terminal state is still in
        // flight; anything terminal has its authoritative output recorded.
        status: isTerminal(item) ? 'result' : 'called',
        metadata: record(item.content.metadata) as ToolMetadata | undefined,
        runId: item.turn_id ?? undefined,
      }
    case 'approval': {
      const args = asText(item.content.arguments)
      if (isTerminal(item)) {
        // A resolved approval is HISTORY. Rendering it as an approval card
        // again would show the user a live prompt for a decision they
        // already made (and which the server will not accept twice), so it
        // becomes a record of the decision instead.
        const decision =
          typeof item.content.decision === 'string' ? item.content.decision : item.status
        return {
          type: 'tool',
          call_id: item.item_id,
          tool: text(item.content.tool) || 'tool',
          input: args,
          output: decision,
          status: 'result',
          metadata: { display_type: 'approval_resolved', decision },
          runId: item.turn_id ?? undefined,
        }
      }
      if (item.content.approval_kind === 'mcp') {
        return {
          type: 'mcp_approval',
          request_id:
            typeof item.content.request_id === 'string'
              ? item.content.request_id
              : item.item_id,
          server_label: text(item.content.server_label),
          tool: text(item.content.tool) || 'tool',
          argumentsText: args,
          session_id: item.thread_id,
        }
      }
      return {
        type: 'approval',
        call_id:
          typeof item.content.call_id === 'string' ? item.content.call_id : item.item_id,
        tool: text(item.content.tool) || 'tool',
        argumentsText: args,
        session_id: item.thread_id,
      }
    }
    case 'artifact':
      return {
        type: 'tool',
        call_id: item.item_id,
        tool: 'artifact',
        input: text(item.content.title),
        output: asText(item.content.content),
        status: 'result',
        metadata: { display_type: 'artifact', mode: item.content.mode },
        runId: item.turn_id ?? undefined,
      }
    case 'reasoning':
      // Reasoning has no renderer in this client today. Dropping it here is
      // a rendering choice, not data loss — the item is still in the store
      // and `listItems({ kinds: ['reasoning'] })` returns it.
      return null
    case 'elicitation':
      // Elicitation prompts are driven by the live `elicitation_requested`
      // flow (they need an interactive responder that a historical read
      // cannot provide); resolved ones carry no transcript text.
      return null
    default:
      return null
  }
}

/**
 * Project canonical items onto the client's render model.
 *
 * Input MUST be ordered by `seq` — that is the server's ordering contract
 * (wall-clock timestamps are not monotonic under concurrent tool
 * completions). The function sorts defensively so a caller that merged two
 * pages out of order still renders correctly.
 */
export function normalizeItems(items: ConversationItem[]): MessageItem[] {
  const ordered = [...items].sort((a, b) => a.seq - b.seq)
  const out: MessageItem[] = []
  for (const item of ordered) {
    const normalized = normalizeOne(item)
    if (normalized) out.push(normalized)
  }
  return out
}

/**
 * Merge a freshly-read page into a cached item list, keyed on `item_id`.
 *
 * Canonical ids are deterministic, so an item re-read after an incremental
 * update replaces its cached copy instead of appearing twice. `revision`
 * breaks ties: a lower-revision copy never overwrites a higher one, which
 * makes out-of-order page arrival safe.
 */
export function mergeItems(
  cached: ConversationItem[],
  incoming: ConversationItem[],
): ConversationItem[] {
  const byId = new Map<string, ConversationItem>()
  for (const item of cached) byId.set(item.item_id, item)
  for (const item of incoming) {
    const existing = byId.get(item.item_id)
    if (existing && existing.revision > item.revision) continue
    byId.set(item.item_id, item)
  }
  return [...byId.values()].sort((a, b) => a.seq - b.seq)
}
