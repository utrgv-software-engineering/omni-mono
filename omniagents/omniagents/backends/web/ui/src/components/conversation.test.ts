import { describe, expect, it } from 'vitest'

import type { ConversationItem } from '../rpc/client'
import { isTerminal, mergeItems, normalizeItems, toolKind } from './conversation'

function item(overrides: Partial<ConversationItem> & Pick<ConversationItem, 'seq' | 'kind'>): ConversationItem {
  return {
    item_id: `itm_${overrides.seq}`,
    thread_id: 'thread-1',
    turn_id: 'run-1',
    status: 'completed',
    role: null,
    created_at: 0,
    updated_at: 0,
    completed_at: 0,
    revision: 0,
    content: {},
    source_ref: {},
    long_lived: false,
    source: 'recorded',
    schema_version: 1,
    ...overrides,
  } as ConversationItem
}

describe('normalizeItems', () => {
  it('renders a completed conversation from persisted items alone', () => {
    const rendered = normalizeItems([
      item({ seq: 1, kind: 'user_message', role: 'user', content: { text: 'build it' } }),
      item({
        seq: 2,
        kind: 'tool_call',
        content: {
          tool: 'execute_bash',
          tool_kind: 'command',
          call_id: 'call-1',
          input: '{"command":"ls"}',
          output: 'README.md',
        },
      }),
      item({ seq: 3, kind: 'agent_message', role: 'assistant', content: { text: 'done' } }),
    ])

    expect(rendered).toEqual([
      { type: 'chat', role: 'user', content: 'build it' },
      {
        type: 'tool',
        call_id: 'call-1',
        tool: 'execute_bash',
        input: '{"command":"ls"}',
        output: 'README.md',
        status: 'result',
        metadata: undefined,
        runId: 'run-1',
      },
      { type: 'chat', role: 'assistant', content: 'done' },
    ])
  })

  it('orders by seq, not by array position', () => {
    const rendered = normalizeItems([
      item({ seq: 3, kind: 'agent_message', content: { text: 'third' } }),
      item({ seq: 1, kind: 'user_message', content: { text: 'first' } }),
      item({ seq: 2, kind: 'agent_message', content: { text: 'second' } }),
    ])
    expect(rendered.map((entry) => (entry as { content: string }).content)).toEqual([
      'first',
      'second',
      'third',
    ])
  })

  it('marks a non-terminal tool call as still running', () => {
    const [entry] = normalizeItems([
      item({
        seq: 1,
        kind: 'tool_call',
        status: 'started',
        content: { tool: 'execute_bash', tool_kind: 'command', call_id: 'c1' },
      }),
    ])
    expect(entry).toMatchObject({ type: 'tool', status: 'called', output: undefined })
  })

  it('renders a failed tool call as terminal with its recorded output', () => {
    const [entry] = normalizeItems([
      item({
        seq: 1,
        kind: 'tool_call',
        status: 'failed',
        content: {
          tool: 'read_file',
          tool_kind: 'file',
          call_id: 'c2',
          output: 'ENOENT',
          metadata: { display_type: 'error' },
        },
      }),
    ])
    expect(entry).toMatchObject({
      type: 'tool',
      status: 'result',
      output: 'ENOENT',
      metadata: { display_type: 'error' },
    })
  })

  it('splits tool and MCP approvals onto their own render types', () => {
    const rendered = normalizeItems([
      item({
        seq: 1,
        kind: 'approval',
        status: 'started',
        content: { approval_kind: 'tool', tool: 'execute_bash', call_id: 'c1', arguments: { command: 'rm' } },
      }),
      item({
        seq: 2,
        kind: 'approval',
        status: 'started',
        content: {
          approval_kind: 'mcp',
          tool: 'search',
          request_id: 'req-9',
          server_label: 'docs',
        },
      }),
    ])
    expect(rendered[0]).toMatchObject({ type: 'approval', call_id: 'c1', tool: 'execute_bash' })
    expect(rendered[1]).toMatchObject({
      type: 'mcp_approval',
      request_id: 'req-9',
      server_label: 'docs',
    })
  })

  it('renders a RESOLVED approval as history, not a live prompt', () => {
    // REGRESSION: a decision the user already made must not come back as a
    // prompt they can answer again.
    const rendered = normalizeItems([
      item({
        seq: 1,
        kind: 'approval',
        status: 'completed',
        content: {
          approval_kind: 'tool',
          tool: 'execute_bash',
          call_id: 'c1',
          decision: 'reject',
        },
      }),
    ])
    expect(rendered[0].type).not.toBe('approval')
    expect(rendered[0]).toMatchObject({
      type: 'tool',
      tool: 'execute_bash',
      output: 'reject',
      status: 'result',
    })
  })

  it('renders a resolved MCP approval as history too', () => {
    const [entry] = normalizeItems([
      item({
        seq: 1,
        kind: 'approval',
        status: 'completed',
        content: {
          approval_kind: 'mcp',
          tool: 'search',
          request_id: 'r1',
          server_label: 'docs',
          decision: 'approve',
        },
      }),
    ])
    expect(entry.type).not.toBe('mcp_approval')
    expect(entry).toMatchObject({ type: 'tool', output: 'approve' })
  })

  it('renders artifacts and compaction checkpoints', () => {
    const rendered = normalizeItems([
      item({
        seq: 1,
        kind: 'artifact',
        content: { title: 'Report', mode: 'markdown', content: '# hi' },
      }),
      item({ seq: 2, kind: 'compaction', content: { summary: 'earlier work' } }),
    ])
    expect(rendered[0]).toMatchObject({ type: 'tool', tool: 'artifact', output: '# hi' })
    expect(rendered[1]).toEqual({ type: 'chat', role: 'system', content: 'earlier work' })
  })

  it('drops kinds this client has no renderer for without dropping others', () => {
    const rendered = normalizeItems([
      item({ seq: 1, kind: 'reasoning', content: { summary: 'thinking' } }),
      item({ seq: 2, kind: 'elicitation', content: { message: 'pick one' } }),
      item({ seq: 3, kind: 'agent_message', content: { text: 'answer' } }),
    ])
    expect(rendered).toEqual([{ type: 'chat', role: 'assistant', content: 'answer' }])
  })
})

describe('mergeItems', () => {
  it('replaces a cached item with a newer revision, keyed on canonical id', () => {
    const started = item({
      seq: 1,
      kind: 'tool_call',
      status: 'started',
      revision: 0,
      content: { tool: 'execute_bash', call_id: 'c1' },
    })
    const completed = { ...started, status: 'completed' as const, revision: 1, content: { ...started.content, output: 'ok' } }

    const merged = mergeItems([started], [completed])
    expect(merged).toHaveLength(1)
    expect(merged[0].status).toBe('completed')
    expect(merged[0].revision).toBe(1)
  })

  it('never lets a stale copy overwrite a newer one', () => {
    const newer = item({ seq: 1, kind: 'agent_message', revision: 5, content: { text: 'new' } })
    const older = { ...newer, revision: 2, content: { text: 'old' } }
    const merged = mergeItems([newer], [older])
    expect(merged[0].content.text).toBe('new')
  })

  it('keeps the merged list ordered by seq', () => {
    const merged = mergeItems(
      [item({ seq: 3, kind: 'agent_message' })],
      [item({ seq: 1, kind: 'user_message' }), item({ seq: 2, kind: 'agent_message' })],
    )
    expect(merged.map((entry) => entry.seq)).toEqual([1, 2, 3])
  })
})

describe('item helpers', () => {
  it('classifies terminal statuses', () => {
    expect(isTerminal(item({ seq: 1, kind: 'tool_call', status: 'started' }))).toBe(false)
    expect(isTerminal(item({ seq: 1, kind: 'tool_call', status: 'completed' }))).toBe(true)
    expect(isTerminal(item({ seq: 1, kind: 'tool_call', status: 'failed' }))).toBe(true)
    expect(isTerminal(item({ seq: 1, kind: 'tool_call', status: 'cancelled' }))).toBe(true)
  })

  it('reads the server-stamped tool taxonomy, defaulting to other', () => {
    expect(toolKind(item({ seq: 1, kind: 'tool_call', content: { tool_kind: 'mcp' } }))).toBe('mcp')
    expect(toolKind(item({ seq: 1, kind: 'tool_call' }))).toBe('other')
  })
})
