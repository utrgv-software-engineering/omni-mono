import { describe, expect, it, vi } from 'vitest';

import {
  type CanonicalSessionListTransport,
  loadCanonicalSessionList,
  threadToSessionSummary,
} from './canonical-session-list';

const thread = (overrides: Record<string, unknown> = {}) => ({
  thread_id: 'thread-1',
  user_id: null,
  status: 'active',
  title: 'Canonical title',
  created_at: 10,
  updated_at: 20,
  last_seq: 3,
  turn_count: 1,
  item_count: 3,
  parent_thread_id: null,
  branched_from_item_id: null,
  usage: {},
  compaction: {},
  pinned: false,
  metadata: {},
  source: 'recorded',
  projected_at: 20,
  schema_version: 1,
  ...overrides,
});

describe('canonical session list', () => {
  it('adapts canonical identity, status, title, counts, and timestamps', () => {
    expect(threadToSessionSummary(thread() as never)).toEqual({
      id: 'thread-1',
      created_at: '1970-01-01T00:00:10.000Z',
      archived: false,
      message_count: 3,
      title: 'Canonical title',
      pinned: false,
      first_message: { content: 'Canonical title' },
      last_message: { timestamp: '1970-01-01T00:00:20.000Z' },
    });
  });

  it('lists threads from the runtime index', async () => {
    const transport = {
      request: vi.fn().mockResolvedValue({ threads: [thread()], next_cursor: null, has_more: false, total: 1 }),
      on: vi.fn(() => () => {}),
    } as unknown as CanonicalSessionListTransport;

    await expect(loadCanonicalSessionList(transport, 2, { status: 'active' })).resolves.toEqual([
      expect.objectContaining({ id: 'thread-1', title: 'Canonical title' }),
    ]);
    expect(transport.request).toHaveBeenCalledWith('list_threads', { status: 'active', limit: 2, order: 'desc' });
  });
});
